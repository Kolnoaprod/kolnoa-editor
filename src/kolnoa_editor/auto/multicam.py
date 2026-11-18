"""Multicam automatique : regrouper les rushes qui se chevauchent dans le temps, les synchroniser (moteur audio existant),
choisir les angles. Tout passe par la MÊME timeline : un groupe `MulticamGroup`, des plans multicam (son = maître).

Principe de sûreté : un angle dont la synchro n'est pas fiable n'est JAMAIS choisi automatiquement (`Angle.usable`) ; s'il n'en reste
qu'un, la séquence est montée sans changement d'angle et le dit."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable

from ..sync.apply import plan_audio_sync, sync_group_audio
from ..sync.sources import FileSource
from ..timeline import Scope, Session
from ..timeline.model import Angle, MulticamGroup, Timeline, TimelineError
from ..timeline.ops import AddGroup, add_multicam_clip
from .analysis import RushInfo, speech_moments
from .builder import GenerationReport
from .planner import StyleParams


def _t(r: RushInfo) -> float | None:
    if not r.start_time:
        return None
    try:
        return datetime.fromisoformat(r.start_time).timestamp()
    except ValueError:
        return None


@dataclass
class GroupProposal:
    name: str
    section: str
    members: list[RushInfo]
    master: str                                   # média du maître
    hints: dict[str, float] = field(default_factory=dict)    # média -> décalage attendu (s) d'après l'horodatage
    start: float = 0.0


def propose_groups(rushes: list[RushInfo], min_overlap: float = 5.0, only_section: str | None = None) -> list[GroupProposal]:
    """Rushes d'une même séquence dont les plages [début, début+durée] se chevauchent d'au moins `min_overlap` s = un groupe."""
    out: list[GroupProposal] = []
    by_sec: dict[str, list[RushInfo]] = {}
    for r in rushes:
        if _t(r) is not None and (only_section is None or r.group == only_section):
            by_sec.setdefault(r.group, []).append(r)
    for sec, rs in sorted(by_sec.items()):
        rs = sorted(rs, key=lambda r: (_t(r), r.media))
        cluster: list[RushInfo] = []
        end = 0.0
        def flush():
            if len(cluster) >= 2:
                with_audio = [r for r in cluster if r.has_audio]
                if with_audio:
                    m = max(with_audio, key=lambda r: (r.duration, r.media))
                    t0 = _t(m)
                    out.append(GroupProposal(f"{sec} {len(out) + 1}", sec, list(cluster), m.media,
                                             {r.media: round(_t(r) - t0, 3) for r in cluster}, _t(cluster[0])))
        for r in rs:
            t, e = _t(r), _t(r) + r.duration
            if cluster and t <= end - min_overlap:
                cluster.append(r)
                end = max(end, e)
            else:
                flush()
                cluster, end = [r], e
        flush()
    return sorted(out, key=lambda p: p.start)


def create_group(session: Session, prop: GroupProposal, durations: dict[str, float]) -> str:
    tl = session.tl
    n = len(tl.groups) + 1
    gid = f"g{n:03d}"
    while any(g.id == gid for g in tl.groups):
        n += 1
        gid = f"g{n:03d}"
    angles, master_id = [], ""
    for i, r in enumerate(prop.members, 1):
        a = Angle(id=f"CAM{i}", media=r.media, kind="video", has_audio=r.has_audio,
                  offset=prop.hints.get(r.media, 0.0), status="unsynced", method="metadata")
        if r.media == prop.master:                    # le maître DÉFINIT l'axe du groupe : décalage 0, fiable par construction
            a.offset, a.status, a.method, a.confidence = 0.0, "reference", "reference", 1.0
        angles.append(a)
        if r.media == prop.master:
            master_id = a.id
    g = MulticamGroup(id=gid, master=master_id, name=prop.name, angles=angles)
    session.do(AddGroup(asdict(g)), f"groupe multicam {prop.name}")
    return gid


def sync_group(session: Session, gid: str, media_root, cfg=None, hint_tolerance: float = 90.0,
               source_factory: Callable = FileSource) -> list[str]:
    """Synchro audio de tous les angles du groupe contre le maître (indice = horodatage). Un seul pas d'annulation ; renvoie les lignes de compte rendu."""
    from pathlib import Path
    g = session.tl.group(gid)
    srcs = {}
    lines: list[str] = []
    for a in g.angles:
        if not a.has_audio:
            continue
        try:
            srcs[a.id] = source_factory(Path(media_root) / a.media)
        except Exception as e:                           # noqa: BLE001 — fichier illisible : l'angle reste non synchronisé, dit clairement
            lines.append(f"{a.id}: audio illisible ({e})")
    if g.master not in srcs:
        raise TimelineError("le maître du groupe n'a pas d'audio lisible : synchro impossible")
    hints = {a.id: (a.offset, hint_tolerance) for a in g.angles if a.id != g.master}
    est = sync_group_audio(session.tl, gid, srcs, cfg, hints)
    rep = plan_audio_sync(session.tl, gid, est)
    if rep.op is not None:
        session.do(rep.op, f"synchro audio {gid}")
    return lines + rep.lines


@dataclass(frozen=True)
class AnglePick:
    g_start: float
    g_end: float
    angle: str


def choose_angles(tl: Timeline, gid: str, g_from: float, g_to: float, durations: dict[str, float],
                  style: StyleParams | None = None, moments: list[tuple[float, float]] | None = None) -> list[AnglePick]:
    """Découpe [g_from, g_to] (axe du groupe) en plans de `shot_min`–`shot_max` s et choisit l'angle de chacun.
    Règles : angle utilisable ET couvrant tout le plan ; jamais deux fois de suite le même si un autre existe ; sinon le moins utilisé ;
    égalité → ordre des identifiants (déterministe). `moments` (axe du groupe) borne les plans à ce qui est sonore."""
    st = style or StyleParams()
    g = tl.group(gid)
    usable = [a for a in g.angles if a.usable and a.kind == "video"]
    smin, smax = max(0.3, st.shot_min / max(st.pace, 0.1)), max(0.5, st.shot_max / max(st.pace, 0.1))
    spans = moments if moments is not None else [(g_from, g_to)]
    picks: list[AnglePick] = []
    used: dict[str, int] = {}
    prev = None
    for a0, a1 in sorted(spans):
        pos, end = max(a0, g_from), min(a1, g_to)
        while end - pos >= smin:
            L = min(smax, end - pos)
            if end - pos - L < smin:
                L = end - pos if end - pos <= smax * 1.25 else L
            ok = []
            for a in usable:
                s, e = a.to_angle(pos), a.to_angle(pos + L)
                if s >= 0 and e <= durations.get(a.media, 0.0) + 1e-6:
                    ok.append(a)
            if ok:
                cand = [a for a in ok if a.id != prev] or ok
                best = min(cand, key=lambda a: (used.get(a.id, 0), a.id))
                used[best.id] = used.get(best.id, 0) + 1
                picks.append(AnglePick(round(pos, 3), round(pos + L, 3), best.id))
                prev = best.id
            pos += L
    return picks


def generate_multicam_edit(session: Session, gid: str, picks: list[AnglePick], track: str = "V1", at: int | None = None,
                           label: str = "Monte-moi cette séquence (multicam)") -> GenerationReport:
    """Pose les plans multicam à la suite sur `track` (son = maître du groupe). Groupe `auto`, annulable d'un geste."""
    from .builder import ensure_tracks
    ensure_tracks(session, track, "A1")
    tl = session.tl
    fps = tl.fps
    pos = at if at is not None else max([c.end for c in tl.clips_on(track)], default=0)
    total = max(1, sum(max(1, round((p.g_end - p.g_start) * fps)) for p in picks))
    first = pos
    with session.group(label, "auto", Scope(pos, pos + total, (track,))) as grp:
        for p in picks:
            frames = max(1, round((p.g_end - p.g_start) * fps))
            session.do(add_multicam_clip(session.tl, gid, p.angle, track, pos, frames, p.g_start), f"{p.angle} {p.g_start:.1f}s")
            pos += frames
    return GenerationReport(len(picks), [tl.group(gid).name], first, pos, grp if picks else None)


def master_moments(rush: RushInfo, tl: Timeline, gid: str) -> list[tuple[float, float]]:
    """Moments sonores du maître, convertis sur l'axe du groupe."""
    g = tl.group(gid)
    a = g.angle(g.master)
    return [(round(a.to_group(m.start), 3), round(a.to_group(m.end), 3)) for m in rush.moments]
