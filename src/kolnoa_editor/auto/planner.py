"""Plan de montage : suite de plans choisis (média, entrée, durée) selon des règles locales et un rythme paramétrable.
Pur et déterministe : mêmes rushes + mêmes paramètres = même plan. Ne touche à aucune timeline."""

from __future__ import annotations

from dataclasses import dataclass

from .analysis import Moment, RushInfo


@dataclass(frozen=True)
class StyleParams:
    shot_min: float = 2.0          # durée mini d'un plan (s)
    shot_max: float = 6.0          # durée maxi : au-delà on coupe (rythme)
    target_s: float | None = None  # durée visée de la séquence (None = tout ce qui est retenu)
    min_score: float = 0.3
    ambient_len: float = 2.5       # un rush sans parole détectée apporte un plan d'ambiance de cette durée
    pace: float = 1.0              # >1 : plus rapide (plans plus courts)


@dataclass(frozen=True)
class Shot:
    media: str
    src_in: float
    length: float
    section: str                   # séquence d'origine (dossier) : génération progressive par section
    score: float = 1.0


def _order_key(r: RushInfo):
    return (r.start_time or "", r.media)


def plan_sequence(rushes: list[RushInfo], style: StyleParams | None = None, only_group: str | None = None) -> list[Shot]:
    st = style or StyleParams()
    smax, smin = max(0.5, st.shot_max / max(st.pace, 0.1)), max(0.3, st.shot_min / max(st.pace, 0.1))
    shots: list[Shot] = []
    for r in sorted(rushes, key=_order_key):
        if only_group and r.group != only_group:
            continue
        moms = [m for m in r.moments if m.score >= st.min_score]
        if not moms:
            L = min(st.ambient_len, r.duration)
            if L >= 0.3:
                moms = [Moment(0.0, L, 0.4, "ambient")]
        for m in sorted(moms, key=lambda m: m.start):
            pos = m.start
            while m.end - pos >= smin:
                L = min(smax, m.end - pos)
                if m.end - pos - L < smin:               # évite un reliquat trop court : on absorbe ou on s'arrête
                    L = m.end - pos if m.end - pos <= smax * 1.25 else L
                shots.append(Shot(r.media, round(pos, 3), round(L, 3), r.group or "rushes", m.score))
                pos += L
    if st.target_s is not None:
        shots = _fit(shots, st.target_s)
    return shots


def _fit(shots: list[Shot], target: float) -> list[Shot]:
    """Garde les plans les mieux notés jusqu'à la durée visée, puis rétablit l'ordre chronologique."""
    if sum(s.length for s in shots) <= target:
        return shots
    idx = sorted(range(len(shots)), key=lambda i: (-shots[i].score, i))
    kept, tot = set(), 0.0
    for i in idx:
        if tot + shots[i].length <= target + 1e-9:
            kept.add(i)
            tot += shots[i].length
    return [s for i, s in enumerate(shots) if i in kept]
