"""Sélection partagée : ce que le réalisateur désigne (point, zone, plans) et que souris, clavier ET consignes lisent de la même façon.

Une sélection est une valeur immuable. Elle porte une `signature` de la timeline au moment où elle a été faite : une commande
interactive qui l'utilise doit vérifier `is_fresh(tl)` (sinon la timeline a bougé entre-temps et la désignation n'est plus fiable).
Indépendant des ancres A1 (celles-ci servent aux consignes persistantes)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .model import Timeline, TimelineError
from .provenance import Scope


def timeline_signature(tl: Timeline) -> str:
    return hashlib.sha1(json.dumps(tl.canonical(), sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Selection:
    kind: str                                   # 'point' | 'range' | 'clips'
    start: int = 0
    end: int = 0
    tracks: tuple[str, ...] | None = None
    clip_ids: tuple[str, ...] = ()
    signature: str = ""

    @staticmethod
    def point(tl: Timeline, frame: int, tracks=None) -> "Selection":
        if frame < 0:
            raise TimelineError("point de sélection négatif")
        return Selection("point", frame, frame, _t(tracks), (), timeline_signature(tl))

    @staticmethod
    def range(tl: Timeline, a: int, b: int, tracks=None) -> "Selection":
        a, b = min(a, b), max(a, b)
        if a < 0 or a == b:
            raise TimelineError("zone de sélection vide ou négative")
        return Selection("range", a, b, _t(tracks), (), timeline_signature(tl))

    @staticmethod
    def of_clips(tl: Timeline, ids) -> "Selection":
        ids = tuple(sorted(set(ids)))
        if not ids:
            raise TimelineError("sélection de plans vide")
        cl = [tl.clip(i) for i in ids]
        return Selection("clips", min(c.start for c in cl), max(c.end for c in cl),
                         tuple(sorted({c.track for c in cl})), ids, timeline_signature(tl))

    def clips_in(self, tl: Timeline) -> list[str]:
        """Plans désignés : les ids pour 'clips', sinon ceux qui INTERSECTENT la zone (ou contiennent le point), triés par début."""
        if self.kind == "clips":
            return [i for i in self.clip_ids if any(c.id == i for c in tl.clips)]
        out = []
        for c in tl.clips:
            if self.tracks is not None and c.track not in self.tracks:
                continue
            hit = c.start <= self.start < c.end if self.kind == "point" else (c.start < self.end and c.end > self.start)
            if hit:
                out.append(c)
        return [c.id for c in sorted(out, key=lambda c: (c.start, c.track, c.id))]

    def is_fresh(self, tl: Timeline) -> bool:
        return self.signature == timeline_signature(tl)

    def require_fresh(self, tl: Timeline) -> None:
        if not self.is_fresh(tl):
            raise TimelineError("la timeline a changé depuis la sélection : sélectionnez à nouveau")

    def to_scope(self, ripple: bool = False) -> Scope:
        if self.kind == "point":
            raise TimelineError("un point ne délimite pas une zone : choisissez une zone ou des plans")
        return Scope(self.start, self.end, self.tracks, ripple)


def _t(tracks):
    return None if tracks is None else tuple(sorted(tracks))
