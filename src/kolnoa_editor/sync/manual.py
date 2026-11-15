"""Alignement manuel : l'humain a toujours le dernier mot.

Un repère = « cet instant de l'angle (t_angle) correspond à cet instant du groupe (t_groupe) » (ex. le flash vu côte
à côte dans le drone et dans la caméra). Un repère donne le décalage ; deux repères assez éloignés donnent aussi la
dérive. Le réglage fin se fait ensuite image par image (`nudge_angle`).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..timeline.model import Timeline, TimelineError
from ..timeline.ops import SetAngleSync


@dataclass
class ManualSolution:
    offset: float
    drift_ppm: float
    residual_s: float          # écart quadratique moyen des repères à la droite (0 avec 1 ou 2 repères)
    drift_measured: bool


def solve_points(points: list, drift_ppm: float = 0.0, min_span_s: float = 30.0,
                 max_drift_ppm: float = 5000.0) -> ManualSolution:
    """Décalage (et dérive si ≥ 2 repères éloignés d'au moins `min_span_s`) à partir de repères [[t_angle, t_groupe]]."""
    pts = [(float(a), float(g)) for a, g in points]
    if not pts:
        raise TimelineError("aucun repère manuel")
    if len({round(a, 6) for a, _ in pts}) != len(pts):
        raise TimelineError("deux repères au même instant de l'angle")
    span = max(a for a, _ in pts) - min(a for a, _ in pts)
    if len(pts) >= 2 and span >= min_span_s:
        n = len(pts)
        ma = sum(a for a, _ in pts) / n
        mg = sum(g for _, g in pts) / n
        sxx = sum((a - ma) ** 2 for a, _ in pts)
        slope = sum((a - ma) * (g - mg) for a, g in pts) / sxx          # = 1 + dérive
        d = (slope - 1.0) * 1e6
        if abs(d) > max_drift_ppm:
            raise TimelineError(f"dérive de {d:.0f} ppm invraisemblable (> {max_drift_ppm:.0f}) : repères à revoir")
        icpt = mg - slope * ma
        res = (sum((g - (icpt + slope * a)) ** 2 for a, g in pts) / n) ** 0.5
        return ManualSolution(icpt, d, res, True)
    if len(pts) >= 2:
        raise TimelineError(f"repères trop proches ({span:.1f} s < {min_span_s:.0f} s) pour mesurer une dérive : "
                            "n'en garder qu'un, ou en placer un second plus loin dans le clip")
    a, g = pts[0]
    return ManualSolution(g - a * (1.0 + drift_ppm * 1e-6), drift_ppm, 0.0, False)


def align_manual(tl: Timeline, group_id: str, angle_id: str, points: list, snap_to_frames: bool = False,
                 min_span_s: float = 30.0) -> SetAngleSync:
    """Opération réversible qui applique un alignement par repères (état « manual », confiance 1)."""
    a = tl.group(group_id).angle(angle_id)
    sol = solve_points(points, a.drift_ppm, min_span_s)
    offset = sol.offset
    if snap_to_frames:
        offset = round(round(offset * tl.fps) / tl.fps, 6)
    return SetAngleSync(group_id, angle_id, {
        "offset": round(offset, 6), "drift_ppm": round(sol.drift_ppm, 3), "method": "manual", "status": "manual",
        "confidence": 1.0, "points": [list(map(float, p)) for p in points]})


def source_times_at(tl: Timeline, group_id: str, t_group: float) -> dict[str, float | None]:
    """Instant source de chaque angle au temps `t_group` de l'axe commun (None si l'instant tombe hors de sa plage
    de validité). Sert à ouvrir côte à côte les deux vues à aligner et à avancer image par image."""
    out: dict[str, float | None] = {}
    for a in tl.group(group_id).angles:
        t = a.to_angle(t_group)
        valid = t >= 0 and (a.range is None or a.range[0] <= t <= a.range[1])
        out[a.id] = round(t, 6) if valid else None
    return out
