"""Applique des résultats de synchro à un groupe, sans jamais écraser le travail de l'humain ni inventer."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..ffmpeg import CancelToken
from ..timeline.model import Timeline
from ..timeline.ops import SetAngleSync, Transaction
from .audio import SyncEstimate, estimate_audio_sync
from .config import SyncConfig


@dataclass
class AppliedReport:
    op: Transaction | None
    lines: list[str] = field(default_factory=list)


def plan_audio_sync(tl: Timeline, group_id: str, estimates: dict[str, SyncEstimate],
                    overwrite_manual: bool = False) -> AppliedReport:
    """Transforme des estimations audio en une transaction (un seul pas d'annulation).

    Règles : angle verrouillé = intact ; angle réglé à la main = intact (sauf `overwrite_manual`) ; « impossible » ne
    touche ni offset ni dérive (seulement l'état, pour que personne ne se fie à l'ancien décalage) ; l'angle maître
    n'est jamais modifié.
    """
    g = tl.group(group_id)
    ops: list[SetAngleSync] = []
    lines: list[str] = []
    for angle_id, est in estimates.items():
        a = g.angle(angle_id)
        if a.id == g.master:
            continue
        if a.locked:
            lines.append(f"{angle_id}: verrouillé, inchangé")
            continue
        if a.status == "manual" and not overwrite_manual:
            lines.append(f"{angle_id}: réglé à la main, inchangé (proposition : {est.status}, "
                         f"décalage {est.offset:+.3f} s, confiance {est.confidence:.2f})")
            continue
        note = " ; ".join(est.notes)
        if est.status == "impossible":
            props = {"status": "impossible", "confidence": 0.0, "method": "audio", "note": note}
            lines.append(f"{angle_id}: synchro IMPOSSIBLE ({note or 'aucune correspondance'})")
        else:
            props = {"offset": round(est.offset, 6), "drift_ppm": round(est.drift_ppm, 3),
                     "confidence": est.confidence, "status": est.status, "method": "audio", "note": note}
            lines.append(f"{angle_id}: {est.status}, décalage {est.offset:+.3f} s, dérive {est.drift_ppm:+.1f} ppm, "
                         f"confiance {est.confidence:.2f}" + (f" — {note}" if note else ""))
        ops.append(SetAngleSync(group_id, angle_id, props))
    return AppliedReport(Transaction(list(ops), f"synchro audio {group_id}") if ops else None, lines)


def sync_group_audio(tl: Timeline, group_id: str, sources: dict, cfg: SyncConfig | None = None,
                     hints: dict[str, tuple[float, float]] | None = None,
                     cancel: CancelToken | None = None) -> dict[str, SyncEstimate]:
    """Estime tous les angles audio du groupe contre son maître. `sources` : angle_id -> source audio."""
    g = tl.group(group_id)
    ref = sources[g.master]
    out: dict[str, SyncEstimate] = {}
    for a in g.angles:
        if a.id == g.master or not a.has_audio or a.id not in sources:
            continue
        out[a.id] = estimate_audio_sync(ref, sources[a.id], cfg, (hints or {}).get(a.id), cancel)
    return out


def split_discontinuous(tl: Timeline, group_id: str, angle_id: str, est: SyncEstimate) -> AppliedReport:
    """Un clip dont l'audio saute (enregistreur coupé puis relancé, fichier recollé) donne plusieurs segments au
    décalage différent. Chaque segment devient une entrée d'angle avec sa plage de validité (`range`) : l'entrée
    d'origine garde le premier segment, les autres s'appellent `<angle>.2`, `<angle>.3`… Les segments restent
    « incertain » tant qu'un humain ne les a pas validés."""
    from dataclasses import asdict
    from ..timeline.model import Angle, TimelineError
    from ..timeline.ops import AddAngle
    g = tl.group(group_id)
    a = g.angle(angle_id)
    if a.locked:
        return AppliedReport(None, [f"{angle_id}: verrouillé, inchangé"])
    if len(est.segments) < 2:
        raise TimelineError("pas de discontinuité à découper")
    ops: list = []
    lines: list[str] = []
    for k, seg in enumerate(est.segments, start=1):
        props = {"offset": round(seg.offset, 6), "drift_ppm": round(seg.drift_ppm, 3), "status": "incertain",
                 "method": "audio", "confidence": min(est.confidence, 0.69),
                 "range": [round(seg.t_start, 3), round(seg.t_end, 3)],
                 "note": f"segment {k}/{len(est.segments)} d'un clip discontinu"}
        if k == 1:
            ops.append(SetAngleSync(group_id, angle_id, props))
        else:
            new = Angle(id=f"{angle_id}.{k}", media=a.media, kind=a.kind, has_audio=a.has_audio, **props)
            ops.append(AddAngle(group_id, asdict(new)))
        lines.append(f"{angle_id}{'' if k == 1 else f'.{k}'}: {seg.t_start:.0f}-{seg.t_end:.0f} s de l'angle → "
                     f"décalage {seg.offset:+.3f} s")
    return AppliedReport(Transaction(ops, f"découper {angle_id} en {len(est.segments)} segments"), lines)
