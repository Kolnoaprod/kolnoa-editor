"""Choix d'angle par qualité visuelle, avec hystérésis, durée minimale et repli déterministe.

Principe : on ne coupe pas pour alterner. Chaque seconde du groupe reçoit, pour chaque angle FIABLE et COUVRANT, une qualité 0..1
(netteté relative aux autres angles au même instant + exposition). Une programmation dynamique maximise la qualité totale moins un coût par
changement d'angle (moindre dans une pause de la parole) ; les plans plus courts que la durée minimale sont absorbés. Sans analyse exploitable
(ou critères éteints), l'angle MAÎTRE est gardé : aucune coupe artificielle. Chaque plan garde son explication (scores, motif)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..timeline.model import Angle, Timeline
from .visual import Sample, VisualParams


@dataclass(frozen=True)
class AnglePick:
    g_start: float
    g_end: float
    angle: str
    why: dict | None = field(default=None, compare=False)       # explication (n'entre pas dans la comparaison)


def _expo(mean: float, dark: float, bright: float) -> float:
    e = 1.0
    if mean < 60:
        e -= min(1.0, (60 - mean) / 50)
    elif mean > 200:
        e -= min(1.0, (mean - 200) / 40)
    return max(0.0, e - 0.5 * max(0.0, dark - 0.25) - 0.5 * max(0.0, bright - 0.25))


def _covers(a: Angle, t0: float, t1: float, dur: float) -> bool:
    return a.to_angle(t0) >= -1e-6 and a.to_angle(t1) <= dur + 1e-6


def slot_qualities(angles: list[Angle], samples: dict[str, list[Sample]], slots: list[tuple[float, float]], durations: dict[str, float],
                   p: VisualParams) -> list[dict[str, tuple[float, dict]]]:
    """Par créneau : {angle: (qualité, détail)} pour les angles couvrants. Netteté = rapport à la meilleure du créneau, saturé à 60 %."""
    idx: dict[str, dict[int, Sample]] = {}
    for a in angles:
        m: dict[int, Sample] = {}
        for s in samples.get(a.id, []):
            k = int((a.to_group(s.t_angle) - slots[0][0]) // p.slot_s)
            m.setdefault(k, s)
        idx[a.id] = m
    out = []
    for k, (t0, t1) in enumerate(slots):
        cov = [a for a in angles if _covers(a, t0, t1, durations.get(a.media, 0.0))]
        raw = {a.id: idx[a.id].get(k) for a in cov}
        top = max((s.sharp for s in raw.values() if s is not None), default=0.0)
        row = {}
        for a in cov:
            s = raw[a.id]
            if s is None:
                continue
            rel = (s.sharp / top) if top > 1e-9 else 1.0
            sharp_sc = min(1.0, rel / 0.6)
            expo = _expo(s.mean, s.dark, s.bright)
            gate = min(1.0, expo / 0.5)                  # image inexploitable (noir/brûlée) : la « netteté » mesurée n'est que du bruit
            q = gate * (p.w_sharp * sharp_sc + p.w_expo * expo) / (p.w_sharp + p.w_expo)
            row[a.id] = (q, {"nettete_rel": round(rel, 2), "exposition": round(expo, 2), "luminance": round(s.mean)})
        out.append(row)
    return out


def choose_visual(tl: Timeline, gid: str, g_from: float, g_to: float, durations: dict[str, float], samples: dict[str, list[Sample]],
                  p: VisualParams, pauses: list[tuple[float, float]] | None = None) -> list[AnglePick]:
    g = tl.group(gid)
    usable = [a for a in g.angles if a.usable and a.kind == "video"]
    master = g.angle(g.master)
    n = int((g_to - g_from) // p.slot_s)
    if n < 1 or not usable:
        return []
    slots = [(g_from + i * p.slot_s, g_from + (i + 1) * p.slot_s) for i in range(n)]
    Q = slot_qualities(usable, samples, slots, durations, p) if p.enabled else [{} for _ in slots]
    # repli : un créneau sans mesure → angle maître s'il couvre, sinon le premier angle couvrant (ordre des identifiants), qualité neutre
    for k, (t0, t1) in enumerate(slots):
        if not Q[k]:
            cov = [a for a in usable if _covers(a, t0, t1, durations.get(a.media, 0.0))]
            pick = next((a for a in cov if a.id == master.id), None) or (sorted(cov, key=lambda a: a.id)[0] if cov else None)
            if pick:
                Q[k] = {pick.id: (0.5, {"repli": True})}
    def lam(t: float) -> float:
        return p.pause_penalty if pauses and any(x <= t <= y for x, y in pauses) else p.switch_penalty

    ids = [a.id for a in usable]
    path: list[str | None] = [None] * len(slots)
    k = 0
    while k < len(slots):
        if not Q[k]:
            k += 1
            continue
        end = k
        while end < len(slots) and Q[end]:
            end += 1
        # programmation dynamique sur le tronçon continu [k, end)
        sc = [dict() for _ in range(k, end)]
        bk = [dict() for _ in range(k, end)]
        for i in range(k, end):
            for a in Q[i]:
                q = Q[i][a][0] + (p.master_bonus if a == master.id else 0.0)
                if i == k:
                    sc[0][a], bk[0][a] = q, None
                    continue
                best, arg = None, None
                for b, vb in sc[i - k - 1].items():
                    v = vb - (0.0 if b == a else lam(slots[i][0]))
                    if best is None or v > best + 1e-12 or (abs(v - best) <= 1e-12 and b == a):
                        best, arg = v, b
                sc[i - k][a], bk[i - k][a] = best + q, arg
        last = max(sc[-1], key=lambda x: (round(sc[-1][x], 9), x == master.id, x))
        for i in range(end - 1, k - 1, -1):
            path[i] = last
            last = bk[i - k][last]
        k = end
    runs = _runs(path, slots)
    runs = _absorb_short(runs, Q, slots, p)
    return _explain(runs, Q, slots, master.id, p)


def _runs(path, slots):
    runs, cur = [], None
    for k, a in enumerate(path):
        if a is None:
            cur = None
            continue
        if cur is not None and cur[2] == a and abs(slots[cur[1] - 1][1] - slots[k][0]) < 1e-9:
            cur[1] = k + 1
        else:
            cur = [k, k + 1, a]
            runs.append(cur)
    return runs


def _absorb_short(runs, Q, slots, p: VisualParams):
    """Un plan plus court que `min_dwell_s` est absorbé par le voisin si celui-ci couvre ses créneaux (sinon il reste : la couverture prime)."""
    minn = max(1, round(p.min_dwell_s / p.slot_s))
    changed = True
    while changed:
        changed = False
        for i, r in enumerate(runs):
            if r[1] - r[0] >= minn:
                continue
            for j in (i - 1, i + 1):
                if 0 <= j < len(runs):
                    nb = runs[j]
                    contiguous = (nb[1] == r[0]) if j < i else (r[1] == nb[0])
                    if contiguous and all(nb[2] in Q[k] for k in range(r[0], r[1])):
                        if j < i:
                            nb[1] = r[1]
                        else:
                            nb[0] = r[0]
                        runs.pop(i)
                        changed = True
                        break
            if changed:
                break
    merged = []
    for r in runs:
        if merged and merged[-1][2] == r[2] and merged[-1][1] == r[0]:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    return merged


def _explain(runs, Q, slots, master: str, p: VisualParams) -> list[AnglePick]:
    picks = []
    for a, b, ang in ((r[0], r[1], r[2]) for r in runs):
        agg: dict[str, list[float]] = {}
        for k in range(a, b):
            for x, (q, _) in Q[k].items():
                agg.setdefault(x, []).append(q)
        scores = {x: round(sum(v) / len(v), 2) for x, v in agg.items()}
        fallback = all(d.get("repli") for k in range(a, b) for _, d in [Q[k].get(ang, (0, {}))])
        others = {x: s for x, s in scores.items() if x != ang}
        if fallback:
            reason = "analyse indisponible : angle maître conservé" if ang == master else "analyse indisponible : seul angle couvrant"
        elif not others:
            reason = "seul angle fiable couvrant ce plan"
        elif ang == master and max(others.values(), default=0) <= scores[ang] + 0.1:
            reason = "angle maître conservé (aucune alternative nettement meilleure)"
        else:
            reason = f"meilleure qualité visuelle ({scores[ang]:.2f} contre {max(others.values()):.2f})"
        picks.append(AnglePick(round(slots[a][0], 3), round(slots[b - 1][1], 3), ang,
                               {"scores": scores, "motif": reason, "analyse": "repli" if fallback else "visuelle"}))
    return picks
