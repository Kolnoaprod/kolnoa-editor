"""Résolveur d'ancres (A1-2) : ancre de contenu → position(s) actuelle(s) sur la timeline, avec un ÉTAT TYPÉ.

États (précédence décroissante quand plusieurs s'appliquent ; les autres restent en `flags`) :
  non_supportée    VFR, cadence non entière, vitesse ≠ 1 : jamais de résolution approximative.
  perdue           média manquant / remplacé / absent du registre : on ne devine pas.
  fragmentée       plage : couverture partielle, trouée ou répartie en plusieurs blocs.
  multiple         plusieurs emplacements possibles (même contenu utilisé deux fois) : choix humain obligatoire.
  hors_plan        média utilisable mais aucun plan ne couvre l'instant (contenu retiré du montage).
  contexte_modifié trouvée, mais le voisinage a changé depuis l'ancrage : à reconfirmer.
  exacte           un seul emplacement, couverture complète, contexte identique.
Index : pour chaque clé (media_id ou groupe), plans triés par entrée + maximum cumulé des sorties → O(log n + k)."""

from __future__ import annotations

import hashlib
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from itertools import accumulate

from ..timeline.model import Clip, Timeline
from .anchor import (AXIS_GROUP, AXIS_MEDIA, JOIN_TOL_US, ContentAnchor, RangeAnchor, clip_axis_in_us, clip_axis_out_us,
                     clip_unsupported, context_sig, media_id_of)
from .registry import PRESENT, MediaRegistry
from .timebase import BIAS_US, US, seconds_to_us, us_to_offset

NON_SUPPORTEE, PERDUE, FRAGMENTEE, MULTIPLE, HORS_PLAN, CONTEXTE, EXACTE = (
    "non_supportée", "perdue", "fragmentée", "multiple", "hors_plan", "contexte_modifié", "exacte")
PRECEDENCE = (NON_SUPPORTEE, PERDUE, FRAGMENTEE, MULTIPLE, HORS_PLAN, CONTEXTE, EXACTE)


@dataclass(frozen=True)
class Segment:
    """Morceau de timeline : images [frame, frame_end) du plan `clip_id`, qui montrent la source [us_a, us_b)."""
    clip_id: str
    track: str
    frame: int
    frame_end: int
    us_a: int
    us_b: int


@dataclass(frozen=True)
class Nearest:
    clip_id: str
    track: str
    frame: int
    us: int                  # instant source du bord
    distance_us: int


@dataclass(frozen=True)
class Resolution:
    anchor_id: str
    state: str
    options: tuple[tuple[Segment, ...], ...] = ()   # une option = une collection de segments (1 seule option si non ambigu)
    flags: tuple[str, ...] = ()
    reason: str = ""
    missing_us: int = 0                             # plage : source non couverte
    nearest: Nearest | None = None                  # point hors_plan : bord de plan le plus proche (sur la même source)
    token: str = ""                                 # empreinte de l'état de la timeline à la résolution (fraîcheur)

    def confirm(self, choice=None):
        from .confirm import confirm
        return confirm(self, choice)


def timeline_token(tl: Timeline, reg: MediaRegistry | None = None) -> str:
    h = hashlib.sha1()
    for c in sorted(tl.clips, key=lambda c: c.id):
        h.update(repr((c.id, c.track, c.media, c.start, c.duration, c.src_in, c.group, c.angle,
                       (c.extra or {}).get("speed"))).encode())
    for g in sorted(tl.groups, key=lambda g: g.id):
        h.update(repr((g.id, [(a.id, a.media, a.offset, a.drift_ppm) for a in g.angles])).encode())
    if reg is not None:
        h.update(repr(sorted((e.media_id, e.path_rel, e.status, e.vfr, e.fps) for e in reg.entries.values())).encode())
    return h.hexdigest()[:16]


class _Axis:
    """Index d'une clé : plans triés par entrée, avec maximum cumulé des sorties."""
    def __init__(self, items: list[tuple[int, int, Clip]]):
        items.sort(key=lambda t: (t[0], t[1], t[2].id))
        self.items = items
        self.starts = [t[0] for t in items]
        self.max_end = list(accumulate((t[1] for t in items), max))

    def overlapping(self, lo: int, hi: int, tol: int = 0):
        """Plans dont [in, out) rencontre [lo, hi) (bornes élargies de `tol`)."""
        i = bisect_left(self.max_end, lo - tol + 1)          # premier index où le max des sorties dépasse lo
        j = bisect_right(self.starts, hi + tol - 1)
        return [t for t in self.items[i:j] if t[1] > lo - tol and t[0] < hi + tol]


class Resolver:
    def __init__(self, tl: Timeline, reg: MediaRegistry):
        self.tl, self.reg = tl, reg
        self._idx: dict[tuple[str, str], _Axis] = {}
        buckets: dict[tuple[str, str], list] = {}
        self._unsupported: dict[tuple[str, str], str] = {}
        for c in tl.clips:
            mid = media_id_of(reg, c.media)
            why = clip_unsupported(reg, c)
            keys = []
            if mid is not None:
                keys.append((AXIS_MEDIA, mid))
            if c.group:
                keys.append((AXIS_GROUP, c.group))
            for k in keys:
                if why:
                    self._unsupported.setdefault(k, why)
                    continue
                buckets.setdefault(k, []).append((clip_axis_in_us(tl, c, k[0]), clip_axis_out_us(tl, c, k[0]), c))
        for k, items in buckets.items():
            self._idx[k] = _Axis(items)
        self.token = timeline_token(tl, reg)

    def is_fresh(self) -> bool:
        return self.token == timeline_token(self.tl, self.reg)

    # --- statut du média -----------------------------------------------------------------------------------------------
    def _dead(self, axis: str, key: str) -> str | None:
        if axis == AXIS_GROUP:
            return None if any(g.id == key for g in self.tl.groups) else "groupe inconnu"
        e = self.reg.entries.get(key)
        if e is None:
            return "média inconnu du registre"
        if e.status != PRESENT:
            return f"média {e.status}"
        return None

    def _axis_us_to_frame(self, c: Clip, axis: str, us: int, lo_us: int) -> int:
        """Image de la timeline pour l'instant `us` (axe demandé) dans le plan `c` (dont l'entrée sur cet axe est `lo_us`)."""
        if axis == AXIS_GROUP:
            g = next(g for g in self.tl.groups if g.id == c.group)
            us = seconds_to_us(g.angle(c.angle).to_angle(us / 1_000_000))
            lo_us = seconds_to_us(c.src_in)
        return c.start + min(max(us_to_offset(us - lo_us, self.tl.fps), 0), c.duration - 1)

    # --- point ---------------------------------------------------------------------------------------------------------------
    def resolve(self, a: ContentAnchor | RangeAnchor) -> Resolution:
        if isinstance(a, RangeAnchor):
            return self._resolve_range(a)
        return self._resolve_point(a)

    def _gate(self, a) -> Resolution | None:
        k = (a.axis, a.key)
        if k in self._unsupported and k not in self._idx:
            return Resolution(a.id, NON_SUPPORTEE, reason=self._unsupported[k], token=self.token)
        why = self._dead(a.axis, a.key)
        if why:
            return Resolution(a.id, PERDUE, reason=why, token=self.token)
        return None

    def _resolve_point(self, a: ContentAnchor) -> Resolution:
        stop = self._gate(a)
        if stop:
            return stop
        k = (a.axis, a.key)
        flags = ("clips_non_supportés",) if k in self._unsupported else ()
        ax = self._idx.get(k)
        hits = ax.overlapping(a.us, a.us + 1, JOIN_TOL_US) if ax else []
        cands = [t for t in hits if self._contains(t, a)]
        # jonction (coupe) : l'instant de la coupe appartient au plan de DROITE ; on écarte le plan de gauche contigu
        cands = [t for t in cands if not any(r[2].track == t[2].track and r[2].start == t[2].end
                                             and abs(t[1] - r[0]) <= JOIN_TOL_US for r in cands)]
        opts = []
        for lo, hi, c in cands:
            f = self._axis_us_to_frame(c, a.axis, a.us, lo)
            opts.append((Segment(c.id, c.track, f, f + 1, a.us, a.us + 1),))
        opts = tuple(sorted(opts, key=lambda o: (o[0].track, o[0].frame, o[0].clip_id)))
        if not opts:
            return Resolution(a.id, HORS_PLAN, flags=flags, reason="aucun plan ne couvre cet instant",
                              nearest=self._nearest(a, ax), token=self.token)
        if len(opts) > 1:
            return Resolution(a.id, MULTIPLE, opts, flags + ("doublon",), "même contenu à plusieurs endroits", token=self.token)
        sig = self._ctx(opts[0][0].clip_id)
        if a.ctx and sig != a.ctx:
            return Resolution(a.id, CONTEXTE, opts, flags, "le voisinage du plan a changé", token=self.token)
        return Resolution(a.id, EXACTE, opts, flags, token=self.token)

    def _contains(self, t, a) -> bool:
        """L'instant `a` est-il dans le plan ? Axe media : comparaison entière EXACTE avec la fin rationnelle du plan (pas la fin arrondie)."""
        lo, hi, c = t
        if a.axis == AXIS_MEDIA:
            d = a.us - lo + BIAS_US
            return d >= 0 and d * self.tl.fps < c.duration * US
        return lo - BIAS_US <= a.us < hi

    def _ctx(self, clip_id: str) -> str:
        return context_sig(self.tl, self.reg, self.tl.clip(clip_id))

    def _nearest(self, a, ax) -> Nearest | None:
        if not ax or not ax.items:
            return None
        best = None
        for lo, hi, c in ax.items:
            for edge, frame in ((lo, c.start), (hi, c.end - 1)):
                d = abs(edge - a.us)
                if best is None or d < best.distance_us:
                    best = Nearest(c.id, c.track, frame, edge, d)
        return best

    # --- plage -------------------------------------------------------------------------------------------------------------------
    def _resolve_range(self, a: RangeAnchor) -> Resolution:
        stop = self._gate(a)
        if stop:
            return stop
        k = (a.axis, a.key)
        flags = ["clips_non_supportés"] if k in self._unsupported else []
        ax = self._idx.get(k)
        segs: list[Segment] = []
        for lo, hi, c in (ax.overlapping(a.start_us, a.end_us) if ax else []):
            s, e = max(lo, a.start_us), min(hi, a.end_us)
            if e - s <= JOIN_TOL_US:
                continue
            f0 = self._axis_us_to_frame(c, a.axis, s, lo)
            f1 = c.end if e >= hi - JOIN_TOL_US else c.start + min(
                c.duration, us_to_offset(self._to_clip_us(c, a.axis, e) - seconds_to_us(c.src_in), self.tl.fps))
            if f1 > f0:
                segs.append(Segment(c.id, c.track, f0, f1, s, e))
        if not segs:
            return Resolution(a.id, HORS_PLAN, tuple(), tuple(flags), "aucun plan ne couvre cette plage", missing_us=a.end_us - a.start_us,
                              token=self.token)
        chains = self._chains(segs)
        covered = self._union_len([(s.us_a, s.us_b) for s in segs])
        missing = (a.end_us - a.start_us) - covered
        total = sum(s.us_b - s.us_a for s in segs)
        if total - covered > JOIN_TOL_US * len(segs):
            flags.append("doublon")
        if len(chains) > 1:
            flags.append("dispersée")
        opts = tuple(tuple(ch) for ch in chains)
        if missing > JOIN_TOL_US * (len(segs) + 1):
            return Resolution(a.id, FRAGMENTEE, opts, tuple(flags), "couverture partielle ou trouée", missing_us=missing, token=self.token)
        if "doublon" in flags:
            return Resolution(a.id, MULTIPLE, opts, tuple(flags), "même contenu à plusieurs endroits", token=self.token)
        if len(chains) > 1:
            return Resolution(a.id, FRAGMENTEE, (tuple(s for ch in chains for s in ch),), tuple(flags),
                              "plage répartie en plusieurs blocs", token=self.token)
        first = chains[0][0]
        if a.ctx and self._ctx(first.clip_id) != a.ctx:
            return Resolution(a.id, CONTEXTE, opts, tuple(flags), "le voisinage du plan a changé", token=self.token)
        return Resolution(a.id, EXACTE, opts, tuple(flags), token=self.token)

    def _to_clip_us(self, c: Clip, axis: str, us: int) -> int:
        if axis == AXIS_GROUP:
            g = next(g for g in self.tl.groups if g.id == c.group)
            return seconds_to_us(g.angle(c.angle).to_angle(us / 1_000_000))
        return us

    @staticmethod
    def _union_len(spans: list[tuple[int, int]]) -> int:
        tot, cur_a, cur_b = 0, None, None
        for a, b in sorted(spans):
            if cur_b is None or a > cur_b:
                tot += (cur_b - cur_a) if cur_b is not None else 0
                cur_a, cur_b = a, b
            else:
                cur_b = max(cur_b, b)
        return tot + ((cur_b - cur_a) if cur_b is not None else 0)

    @staticmethod
    def _chains(segs: list[Segment]) -> list[list[Segment]]:
        """Blocs contigus À LA FOIS sur la timeline (même piste, image de fin = image de début) et dans la source (à ±tol)."""
        out: list[list[Segment]] = []
        for s in sorted(segs, key=lambda s: (s.track, s.frame, s.clip_id)):
            for ch in out:
                t = ch[-1]
                if t.track == s.track and t.frame_end == s.frame and abs(t.us_b - s.us_a) <= JOIN_TOL_US:
                    ch.append(s)
                    break
            else:
                out.append([s])
        return out
