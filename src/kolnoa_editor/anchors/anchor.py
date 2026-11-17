"""Ancres de contenu (A1-2) : un point ou une plage de SOURCE, indépendants de la position sur la timeline.

Une ancre ne contient AUCUNE position de timeline : elle dit « cet instant de ce média » (axe `media`) ou « cet instant de ce
groupe multicam » (axe `group`, temps du groupe). La position courante est recalculée par `Resolver` ; les opérations du
moteur ne sont jamais réécrites. Les ancres ne portent ni chemin ni nom de fichier (elles survivent à un déplacement)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..timeline.model import Clip, Timeline
from .registry import MediaRegistry
from .timebase import US, frames_to_us, offset_to_us, seconds_to_us

AXIS_MEDIA, AXIS_GROUP = "media", "group"
JOIN_TOL_US = 2          # tolérance de jonction : une coupe (razor) arrondit chaque côté à ±1 µs
UNSUPPORTED_SPEED = "vitesse ≠ 1 (retiming non supporté)"


class AnchorRefused(ValueError):
    """Création d'ancre refusée (cadence variable, cadence non entière, vitesse ≠ 1, plan inconnu, plage multi-sources…)."""


@dataclass(frozen=True)
class ContentAnchor:
    id: str
    axis: str            # 'media' | 'group'
    key: str             # media_id (axe media) ou id de groupe multicam (axe group)
    us: int              # instant source en µs (temps du média, ou temps du groupe)
    ctx: str = ""        # empreinte du voisinage au moment de la création (détecte « contexte_modifié »), jamais un chemin


@dataclass(frozen=True)
class RangeAnchor:
    """Plage [start, end) d'UNE seule source ; la résolution donne une collection de segments."""
    id: str
    axis: str
    key: str
    start_us: int
    end_us: int
    ctx: str = ""

    def __post_init__(self):
        if self.end_us <= self.start_us:
            raise AnchorRefused("plage vide ou inversée")


def clip_speed_ok(c: Clip) -> bool:
    sp = (c.extra or {}).get("speed")
    return sp in (None, 1, 1.0)


def media_id_of(reg: MediaRegistry, path: str) -> str | None:
    """`media_id` du média de ce chemin : le média PRÉSENT d'abord, sinon le plus récent qui a eu ce chemin (même manquant/remplacé)."""
    mid = reg.id_for_path(path)
    if mid:
        return mid
    hit = [e for e in reg.entries.values() if path in e.locations]
    return hit[-1].media_id if hit else None


def clip_unsupported(reg: MediaRegistry, c: Clip) -> str | None:
    """Raison pour laquelle les ancres ne sont pas supportées sur ce plan, ou None."""
    if not clip_speed_ok(c):
        return UNSUPPORTED_SPEED
    mid = media_id_of(reg, c.media)
    if mid is None:
        return "média absent du registre"
    e = reg.get(mid)
    if e.vfr:
        return "cadence variable (VFR) non supportée"
    if not e.integer_fps:
        return "cadence non entière (23,976 / 29,97…) non supportée"
    return None


def clip_axis_in_us(tl: Timeline, c: Clip, axis: str) -> int:
    """Entrée du plan (µs) sur l'axe demandé."""
    return _axis_us(tl, c, axis, c.src_in)


def clip_axis_out_us(tl: Timeline, c: Clip, axis: str) -> int:
    """Sortie (exclue) du plan (µs) sur l'axe demandé. Sur l'axe media : entrée + durée exacte du plan."""
    if axis == AXIS_MEDIA:
        return seconds_to_us(c.src_in) + frames_to_us(c.duration, tl.fps)
    return _axis_us(tl, c, axis, c.src_in + c.duration / tl.fps)


def _axis_us(tl: Timeline, c: Clip, axis: str, angle_seconds: float) -> int:
    if axis == AXIS_MEDIA:
        return seconds_to_us(angle_seconds)
    g = next((g for g in tl.groups if g.id == c.group), None)
    if g is None or c.angle is None:
        raise AnchorRefused("plan multicam sans groupe")
    return seconds_to_us(g.angle(c.angle).to_group(angle_seconds))


def frame_us(tl: Timeline, c: Clip, frame: int, axis: str) -> int:
    """Instant source (µs, axe demandé) de l'image `frame` de la timeline pour ce plan (peut sortir du plan : sert aux bornes)."""
    base_us = seconds_to_us(c.src_in) + offset_to_us(frame - c.start, tl.fps)
    if axis == AXIS_MEDIA:
        return base_us
    return _axis_us(tl, c, axis, base_us / US)


def key_for(tl: Timeline, reg: MediaRegistry, c: Clip, axis: str) -> str:
    if axis == AXIS_GROUP:
        if not c.group:
            raise AnchorRefused("plan sans groupe multicam")
        return c.group
    mid = media_id_of(reg, c.media)
    if mid is None:
        raise AnchorRefused("média absent du registre")
    return mid


def context_sig(tl: Timeline, reg: MediaRegistry, c: Clip) -> str:
    """Empreinte du voisinage : média/angle du premier plan VOISIN DIFFÉRENT de chaque côté sur la piste (sans chemin ni nom).
    Les morceaux d'un même plan coupé au rasoir ne changent donc pas le contexte ; une autre prise voisine, si."""
    me = (media_id_of(reg, c.media) or "?", c.group or "", c.angle or "")
    same = sorted((x for x in tl.clips if x.track == c.track), key=lambda x: (x.start, x.id))
    i = same.index(c)

    def first_other(seq):
        for x in seq:
            sig = (media_id_of(reg, x.media) or "?", x.group or "", x.angle or "")
            if sig != me:
                return sig
        return None
    side = (first_other(reversed(same[:i])), first_other(same[i + 1:]))
    return hashlib.sha1(repr(side).encode()).hexdigest()[:12]


def _clip_at(tl: Timeline, track: str, frame: int) -> Clip:
    for c in tl.clips:
        if c.track == track and c.start <= frame < c.end:
            return c
    raise AnchorRefused(f"aucun plan sur {track} à l'image {frame}")


def _checked(tl, reg, c, axis):
    why = clip_unsupported(reg, c)
    if why:
        raise AnchorRefused(why)
    return key_for(tl, reg, c, axis)


def anchor_at(tl: Timeline, reg: MediaRegistry, track: str, frame: int, anchor_id: str, axis: str = AXIS_MEDIA) -> ContentAnchor:
    """Ancre le contenu visible à l'image `frame` de la piste (début de cette image)."""
    c = _clip_at(tl, track, frame)
    key = _checked(tl, reg, c, axis)
    return ContentAnchor(anchor_id, axis, key, frame_us(tl, c, frame, axis), context_sig(tl, reg, c))


def range_between(tl: Timeline, reg: MediaRegistry, track: str, first: int, end: int, anchor_id: str,
                  axis: str = AXIS_MEDIA) -> RangeAnchor:
    """Ancre la plage d'images [first, end) d'UNE piste ; refusée si elle traverse des sources différentes ou une zone vide."""
    if end <= first:
        raise AnchorRefused("plage vide ou inversée")
    clips = sorted((c for c in tl.clips if c.track == track and c.start < end and c.end > first), key=lambda x: x.start)
    if not clips or clips[0].start > first:
        raise AnchorRefused("la plage commence hors de tout plan")
    keys = {_checked(tl, reg, c, axis) for c in clips}
    if len(keys) != 1:
        raise AnchorRefused("plage sur plusieurs sources : ancrer chaque plan séparément")
    c0, c1 = clips[0], clips[-1]
    if c1.end < end or any(a.end != b.start for a, b in zip(clips, clips[1:])):
        raise AnchorRefused("la plage contient un trou ou dépasse le dernier plan")
    for a, b in zip(clips, clips[1:]):
        if abs(clip_axis_out_us(tl, a, axis) - clip_axis_in_us(tl, b, axis)) > JOIN_TOL_US:
            raise AnchorRefused("la plage saute dans la source (plans non contigus) : ancrer chaque plan séparément")
    return RangeAnchor(anchor_id, axis, keys.pop(), frame_us(tl, c0, first, axis), frame_us(tl, c1, end, axis),
                       context_sig(tl, reg, c0))
