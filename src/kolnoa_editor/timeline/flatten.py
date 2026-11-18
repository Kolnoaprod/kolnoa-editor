"""Aplatissement vidéo (J1-a) : de la timeline multi-pistes à une suite de segments contigus, un seul plan visible par image.

Règles (voir docs/J1_RENDU_COMPLET_PROPOSITION.md §2) :
  - à chaque image, le plan visible est celui de la piste vidéo la plus HAUTE (dernière dans `Timeline.tracks`) qui en porte un ;
  - une piste vidéo « muette » est masquée (le plan de coupe désactivé ne recouvre rien) ; les pistes audio sont ignorées ;
  - une image sans plan visible est du NOIR explicite (segment `clip_id=None`) : jamais un trou silencieux ;
  - le multicam n'a pas de cas particulier : un plan multicam est un plan dont `media`/`src_in` sont ceux de l'angle montré ;
  - fonction PURE : ne lit aucun média, ne modifie pas la timeline. Les décalages source sont calculés en images (exacts), puis
    convertis en secondes arrondies à la microseconde comme partout dans le moteur."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .model import Clip, Timeline
from .timebase import frames_to_seconds


@dataclass(frozen=True)
class VideoSpan:
    start: int                 # image de timeline, incluse
    end: int                   # exclue
    clip_id: str | None        # None = noir
    track: str | None = None
    media: str | None = None
    src_in: float = 0.0        # secondes dans le média, à l'image `start` du segment
    offset: int = 0            # images déjà « consommées » du plan avant ce segment (≥ 0)
    speed: float = 1.0         # secondes source / seconde timeline

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def is_black(self) -> bool:
        return self.clip_id is None


@dataclass
class Flattened:
    spans: list[VideoSpan]
    total: int                                           # durée en images (jusqu'à la fin du dernier plan visible)
    gaps: list[tuple[int, int]] = field(default_factory=list)   # zones noires [début, fin)
    warnings: list[str] = field(default_factory=list)

    def at(self, frame: int) -> VideoSpan | None:
        lo, hi = 0, len(self.spans)
        while lo < hi:
            mid = (lo + hi) // 2
            s = self.spans[mid]
            if frame < s.start:
                hi = mid
            elif frame >= s.end:
                lo = mid + 1
            else:
                return s
        return None


def visible_video_tracks(tl: Timeline) -> list[str]:
    """Pistes vidéo visibles, de la plus basse à la plus haute."""
    return [t.id for t in tl.tracks if t.kind == "video" and not t.muted]


def flatten_video(tl: Timeline) -> Flattened:
    order = {tid: i for i, tid in enumerate(visible_video_tracks(tl))}
    clips = [c for c in tl.clips if c.track in order]
    warnings: list[str] = []
    hidden = sorted({c.track for c in tl.clips if c.track not in order and tl.track(c.track).kind == "video"})
    if hidden:
        warnings.append(f"pistes vidéo muettes ignorées : {', '.join(hidden)}")
    if not clips:
        return Flattened([], 0, [], warnings)
    total = max(c.end for c in clips)
    cuts = sorted({0, total, *(c.start for c in clips), *(c.end for c in clips)})
    by_start = sorted(clips, key=lambda c: c.start)
    spans: list[VideoSpan] = []
    gaps: list[tuple[int, int]] = []
    active: list[Clip] = []
    i = 0
    for a, b in zip(cuts, cuts[1:]):
        while i < len(by_start) and by_start[i].start <= a:
            active.append(by_start[i])
            i += 1
        active = [c for c in active if c.end > a]
        top = max(active, key=lambda c: order[c.track], default=None)
        if top is None:
            spans.append(VideoSpan(a, b, None))
            if gaps and gaps[-1][1] == a:
                gaps[-1] = (gaps[-1][0], b)
            else:
                gaps.append((a, b))
        else:
            off = a - top.start
            spans.append(VideoSpan(a, b, top.id, top.track, top.media, round(top.src_in + frames_to_seconds(off, tl.fps) * float((top.extra or {}).get('speed', 1.0)), 6), off,
                                   float((top.extra or {}).get('speed', 1.0))))
    if gaps:
        warnings.append("zones sans image (noir) : " + ", ".join(f"{a}–{b}" for a, b in gaps[:5]) + (" …" if len(gaps) > 5 else ""))
    return Flattened(spans, total, gaps, warnings)


@dataclass(frozen=True)
class Issue:
    level: str       # 'error' | 'warning'
    message: str


def check_flatten(tl: Timeline, fl: Flattened | None = None, media_exists: Callable[[str], bool] | None = None,
                  source_len: Callable[[str], float | None] | None = None) -> list[Issue]:
    """Contrôle préalable au rendu. Erreurs : vitesse ≠ 1, média introuvable, plan qui dépasse la fin de sa source.
    Avertissements : noir, pistes masquées, plans entièrement recouverts (inutiles au rendu)."""
    fl = fl or flatten_video(tl)
    out = [Issue("warning", w) for w in fl.warnings]
    used = {s.clip_id for s in fl.spans if s.clip_id}
    for c in tl.clips:
        if tl.track(c.track).kind != "video" or c.track not in visible_video_tracks(tl):
            continue
        speed = (c.extra or {}).get('speed', 1.0)
        if not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2.0 or speed != speed:
            out.append(Issue('error', f'{c.id}: vitesse invalide (plage 0,5×–2×)'))
            continue
        if speed != 1.0 and c.audio == 'group':
            out.append(Issue('error', f'{c.id}: ralentissement multicam avec audio maître non encore supporté'))
        if c.id not in used:
            out.append(Issue("warning", f"{c.id} est entièrement recouvert par une piste supérieure : absent du rendu"))
        if media_exists is not None and not media_exists(c.media):
            out.append(Issue("error", f"{c.id}: média introuvable ({c.media})"))
        elif source_len is not None:
            n = source_len(c.media)
            if n is not None and c.src_in + c.duration / tl.fps * speed > n + 1e-3:
                out.append(Issue("error", f"{c.id}: dépasse la fin de la source ({c.src_in + c.duration / tl.fps * speed:.3f} s > {n:.3f} s)"))
    return out
