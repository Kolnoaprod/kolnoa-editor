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
from .angles import AnglePick, choose_visual
from .planner import StyleParams
from .visual import AnalysisStats, VisualParams, analyze_angle


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


def choose_angles(tl: Timeline, gid: str, g_from: float, g_to: float, durations: dict[str, float],
                  style: StyleParams | None = None, moments: list[tuple[float, float]] | None = None,
                  samples: dict | None = None, params: VisualParams | None = None) -> list[AnglePick]:
    """Choix d'angle sur l'axe du groupe. Avec `samples` (analyse visuelle) : qualité + hystérésis ; sans analyse (ou critères éteints) :
    l'angle MAÎTRE sur toute la durée où il couvre, sinon le seul angle fiable couvrant — jamais d'alternance forcée.
    `moments` (sonores) : un tronçon par moment ; deux moments séparés de ≤ 2 s ne font qu'un tronçon (la pause y rend la coupe moins coûteuse)."""
    p = params or VisualParams()
    if style is not None and params is None:
        p = VisualParams(min_dwell_s=max(p.min_dwell_s, style.shot_min))
    spans = _merge_spans(sorted(moments if moments is not None else [(g_from, g_to)]), 2.0)
    picks: list[AnglePick] = []
    for a0, a1, pauses in spans:
        a0, a1 = max(a0, g_from), min(a1, g_to)
        if a1 - a0 >= p.slot_s:
            picks += choose_visual(tl, gid, a0, a1, durations, samples or {}, p if samples else VisualParams(**{**p.__dict__, "enabled": False}), pauses)
    return picks


def _merge_spans(spans, gap):
    out = []
    for a, b in spans:
        if out and a - out[-1][1] <= gap:
            out[-1][2].append((out[-1][1], a))
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b, []])
    return [(a, b, pz) for a, b, pz in out]


def analyze_group(session: Session, gid: str, media_root, g_from: float, g_to: float, durations: dict[str, float],
                  params: VisualParams, cache_dir=None, proxy_for: Callable | None = None) -> tuple[dict, AnalysisStats]:
    """Échantillons visuels des angles fiables sur [g_from, g_to] (une passe ffmpeg par angle, mise en cache)."""
    from pathlib import Path
    stats = AnalysisStats()
    out: dict = {}
    for a in session.tl.group(gid).angles:
        if not a.usable or a.kind != "video":
            continue
        t0, t1 = max(0.0, a.to_angle(g_from)), min(durations.get(a.media, 0.0), a.to_angle(g_to))
        try:
            src = (proxy_for(a.media) if proxy_for else None) or Path(media_root) / a.media      # proxy à jour s'il existe : bien moins coûteux à décoder
            out[a.id] = analyze_angle(src, t0, t1, params, Path(cache_dir) if cache_dir else None, stats)
        except Exception as e:                           # noqa: BLE001 — angle illisible : traité comme sans mesure (repli), dit dans le compte rendu
            stats.notes.append(f"{a.id}: analyse impossible ({e})")
    return out, stats


def generate_multicam_edit(session: Session, gid: str, picks: list[AnglePick], track: str = "V1", at: int | None = None,
                           label: str = "Monte-moi cette séquence (multicam)") -> GenerationReport:
    """Pose les plans multicam à la suite sur `track` (son = maître du groupe). Groupe `auto`, annulable d'un geste."""
    from .builder import ensure_tracks
    ensure_tracks(session, track, "A1")
    tl = session.tl
    fps = tl.fps
    pos = at if at is not None else max([c.end for c in tl.clips_on(track)], default=0)
    total = max(1, sum(max(1, round((p.g_end - p.g_start) * fps)) for p in picks))
    if not picks:
        return GenerationReport(0, [tl.group(gid).name], pos, pos, None)
    first = pos
    with session.group(label, "auto", Scope(pos, pos + total, (track,))) as grp:
        for p in picks:
            frames = max(1, round((p.g_end - p.g_start) * fps))
            op = add_multicam_clip(session.tl, gid, p.angle, track, pos, frames, p.g_start)
            if p.why:
                op.clip["extra"] = {**(op.clip.get("extra") or {}), "angle_choice": p.why}
            session.do(op, f"{p.angle} {p.g_start:.1f}s")
            pos += frames
    return GenerationReport(len(picks), [tl.group(gid).name], first, pos, grp if picks else None)


def master_moments(rush: RushInfo, tl: Timeline, gid: str) -> list[tuple[float, float]]:
    """Moments sonores du maître, convertis sur l'axe du groupe."""
    g = tl.group(gid)
    a = g.angle(g.master)
    return [(round(a.to_group(m.start), 3), round(a.to_group(m.end), 3)) for m in rush.moments]
