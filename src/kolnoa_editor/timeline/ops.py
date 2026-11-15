"""Opérations réversibles sur la timeline.

Chaque opération : `apply(tl)` modifie la timeline et RENVOIE l'opération inverse ;
`describe()` donne le diff lisible ; `to_dict()/op_from_dict()` la rendent journalisable.
Un `Transaction` regroupe plusieurs opérations en un seul pas d'annulation, de façon atomique.
`execute()` applique une opération, valide la timeline et revient en arrière si elle devient invalide.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from typing import Any, ClassVar

from .model import (AUDIO_MODES, DEFAULT_TRANSITION_FRAMES, Clip, LockedError, Marker, Timeline, TimelineError, Track, Transition)
from .timebase import seconds_to_frames

_REGISTRY: dict[str, type] = {}
MIN_PIECE = 1  # image : plus petit morceau acceptable lors d'une scission


def register(cls):
    _REGISTRY[cls.type] = cls
    return cls


class Op:
    type: ClassVar[str] = ""

    def apply(self, tl: Timeline) -> "Op":  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def to_dict(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self)}  # type: ignore[arg-type]
        return {"type": self.type, **d}


def op_from_dict(d: dict) -> Op:
    try:
        cls = _REGISTRY[d["type"]]
    except KeyError as exc:
        raise TimelineError(f"Opération inconnue : {d.get('type')!r}") from exc
    args = {k: v for k, v in d.items() if k != "type"}
    if cls is Transaction:
        args["ops"] = [op_from_dict(o) for o in args.get("ops", [])]
    try:
        return cls(**args)
    except TypeError as exc:
        raise TimelineError(f"Opération {d['type']} mal formée : {exc}") from exc


# --- verrous ------------------------------------------------------------
def _writable(tl: Timeline, clip: Clip) -> None:
    if clip.locked:
        raise LockedError(f"{clip.id} est verrouillé")
    if tl.track(clip.track).locked:
        raise LockedError(f"la piste {clip.track} est verrouillée ({clip.id})")


def _clip_from(d: dict) -> Clip:
    try:
        return Clip(**d)
    except TypeError as exc:
        raise TimelineError(f"Plan mal formé : {exc}") from exc


def _fmt(tl_fps: int | None, frames: int) -> str:
    return f"{frames} i" if not tl_fps else f"{frames / tl_fps:.2f} s"


# --- pistes -------------------------------------------------------------
@register
@dataclass
class AddTrack(Op):
    type: ClassVar[str] = "add_track"
    track: dict

    def apply(self, tl):
        t = Track(**self.track)
        if any(x.id == t.id for x in tl.tracks):
            raise TimelineError(f"piste déjà existante : {t.id}")
        tl.tracks.append(t)
        return RemoveTrack(t.id)

    def describe(self):
        return f"+ piste {self.track['id']} ({self.track.get('kind', 'video')})"


@register
@dataclass
class RemoveTrack(Op):
    type: ClassVar[str] = "remove_track"
    track_id: str

    def apply(self, tl):
        t = tl.track(self.track_id)
        if any(c.track == t.id for c in tl.clips):
            raise TimelineError(f"la piste {t.id} n'est pas vide")
        if t.locked:
            raise LockedError(f"la piste {t.id} est verrouillée")
        tl.tracks.remove(t)
        return AddTrack(asdict(t))

    def describe(self):
        return f"- piste {self.track_id}"


# --- plans --------------------------------------------------------------
@register
@dataclass
class AddClip(Op):
    type: ClassVar[str] = "add_clip"
    clip: dict
    restore: bool = False   # vrai seulement pour une annulation : rend l'id supprimé à son plan

    def apply(self, tl):
        c = _clip_from(self.clip)
        if any(x.id == c.id for x in tl.clips):
            raise TimelineError(f"id de plan déjà utilisé : {c.id}")
        if self.restore:
            if c.id not in tl.retired:
                raise TimelineError(f"{c.id}: rien à restaurer")
        elif c.id in tl._used_clip_ids():
            raise TimelineError(f"id de plan déjà utilisé ou supprimé : {c.id}")
        if tl.track(c.track).locked:
            raise LockedError(f"la piste {c.track} est verrouillée")
        if self.restore:
            tl.retired.remove(c.id)
        tl.clips.append(c)
        return RemoveClip(c.id)

    def describe(self):
        c = self.clip
        return f"+ {c['id']} : {c['media']} {c['duration']} i sur {c['track']} à l'image {c['start']}"


@register
@dataclass
class RemoveClip(Op):
    type: ClassVar[str] = "remove_clip"
    clip_id: str

    def apply(self, tl):
        c = tl.clip(self.clip_id)
        _writable(tl, c)
        attached = tl.transitions_of(c.id)          # une coupe qui disparaît emporte ses transitions
        for t in attached:
            tl.transitions.remove(t)
        tl.clips.remove(c)
        tl.retired.append(c.id)
        restore = AddClip(asdict(c), restore=True)
        if not attached:
            return restore
        return Transaction([restore] + [AddTransition(asdict(t)) for t in attached])

    def describe(self):
        return f"- {self.clip_id}"


@register
@dataclass
class MoveClip(Op):
    type: ClassVar[str] = "move_clip"
    clip_id: str
    start: int
    track: str | None = None

    def apply(self, tl):
        c = tl.clip(self.clip_id)
        _writable(tl, c)
        old = MoveClip(c.id, c.start, c.track)
        if self.track is not None:
            tl.track(self.track)
            if tl.track(self.track).locked:
                raise LockedError(f"la piste {self.track} est verrouillée")
            c.track = self.track
        c.start = self.start
        return old

    def describe(self):
        return f"~ {self.clip_id} déplacé à l'image {self.start}" + (f" sur {self.track}" if self.track else "")


@register
@dataclass
class TrimClip(Op):
    """Rogne un plan : nouvelle position, durée et point d'entrée source (valeurs absolues)."""
    type: ClassVar[str] = "trim_clip"
    clip_id: str
    start: int
    duration: int
    src_in: float

    def apply(self, tl):
        c = tl.clip(self.clip_id)
        _writable(tl, c)
        if self.duration <= 0:
            raise TimelineError(f"{c.id}: durée invalide ({self.duration} images)")
        if c.fade_in + c.fade_out > self.duration / tl.fps + 1e-6:   # les fondus doivent tenir dans le plan
            raise TimelineError(f"{c.id}: les fondus dépassent la nouvelle durée")
        old = TrimClip(c.id, c.start, c.duration, c.src_in)
        c.start, c.duration, c.src_in = self.start, self.duration, round(self.src_in, 6)
        return old

    def describe(self):
        return f"~ {self.clip_id} rogné : image {self.start}, {self.duration} i, source {self.src_in:.2f} s"


@register
@dataclass
class SplitClip(Op):
    type: ClassVar[str] = "split_clip"
    clip_id: str
    at: int                     # image de timeline (strictement à l'intérieur du plan)
    new_id: str | None = None
    restore_id: bool = False

    def apply(self, tl):
        c = tl.clip(self.clip_id)
        _writable(tl, c)
        if not (c.start + MIN_PIECE <= self.at <= c.end - MIN_PIECE):
            raise TimelineError(f"{c.id}: scission à l'image {self.at} impossible "
                                f"(plan {c.start}-{c.end})")
        new_id = self.new_id or tl.next_clip_id()
        if self.restore_id:
            if new_id not in tl.retired:
                raise TimelineError(f"{new_id}: rien à restaurer")
            tl.retired.remove(new_id)
        elif new_id in tl._used_clip_ids():
            raise TimelineError(f"id de plan déjà utilisé : {new_id}")
        original = asdict(c)
        offset = (self.at - c.start) / tl.fps
        right = Clip(**{**original, "id": new_id, "start": self.at, "duration": c.end - self.at,
                        "src_in": round(c.src_in + offset, 6), "fade_in": 0.0, "parent": c.id,
                        "extra": dict(c.extra)})
        c.duration, c.fade_out = self.at - c.start, 0.0
        tl.clips.insert(tl.clips.index(c) + 1, right)
        for t in tl.transitions:                    # la transition de sortie suit la fin du plan
            if t.from_clip == c.id:
                t.from_clip = new_id
        return JoinClips(c.id, new_id, original)

    def describe(self):
        return f"~ {self.clip_id} scindé à l'image {self.at}"


@register
@dataclass
class JoinClips(Op):
    """Inverse d'une scission : rend au plan de gauche son état d'origine et supprime celui de droite."""
    type: ClassVar[str] = "join_clips"
    left_id: str
    right_id: str
    original: dict

    def apply(self, tl):
        left, right = tl.clip(self.left_id), tl.clip(self.right_id)
        _writable(tl, left)
        _writable(tl, right)
        if right.parent != left.id or left.end != right.start:
            raise TimelineError(f"{left.id} et {right.id} ne sont pas contigus")
        at = right.start
        inner = tl.transition_at(left.id, right.id)  # transition posée sur la coupe qu'on referme
        if inner is not None:
            tl.transitions.remove(inner)
        for t in tl.transitions:
            if t.from_clip == right.id:
                t.from_clip = left.id
        idx = tl.clips.index(left)
        tl.clips[idx] = _clip_from(self.original)
        tl.clips.remove(right)
        tl.retired.append(right.id)
        split = SplitClip(left.id, at, right.id, restore_id=True)
        return split if inner is None else Transaction([split, AddTransition(asdict(inner))])

    def describe(self):
        return f"~ {self.left_id} et {self.right_id} réunis"


@register
@dataclass
class ShiftClips(Op):
    type: ClassVar[str] = "shift_clips"
    clip_ids: list[str]
    delta: int

    def apply(self, tl):
        clips = [tl.clip(i) for i in self.clip_ids]
        for c in clips:
            _writable(tl, c)
        for c in clips:
            c.start += self.delta
        return ShiftClips(list(self.clip_ids), -self.delta)

    def describe(self):
        return f"~ {len(self.clip_ids)} plan(s) décalé(s) de {self.delta} i"


_CLIP_PROPS = {"audio", "fade_in", "fade_out", "gain_db", "note", "label", "locked", "extra", "media"}


@register
@dataclass
class SetClipProps(Op):
    type: ClassVar[str] = "set_clip_props"
    clip_id: str
    props: dict[str, Any]

    def apply(self, tl):
        c = tl.clip(self.clip_id)
        unknown = set(self.props) - _CLIP_PROPS
        if unknown:
            raise TimelineError(f"propriétés de plan inconnues : {sorted(unknown)}")
        if set(self.props) != {"locked"}:       # (dé)verrouiller reste toujours possible
            _writable(tl, c)
        old = {k: getattr(c, k) for k in self.props}
        if "audio" in self.props and self.props["audio"] not in AUDIO_MODES:
            raise TimelineError(f"{c.id}: audio doit être 'source' ou 'mute'")
        for k, v in self.props.items():
            setattr(c, k, v)
        return SetClipProps(c.id, old)

    def describe(self):
        return f"~ {self.clip_id} : " + ", ".join(f"{k}={v!r}" for k, v in self.props.items())


@register
@dataclass
class SetTrackProps(Op):
    type: ClassVar[str] = "set_track_props"
    track_id: str
    props: dict[str, Any]

    def apply(self, tl):
        t = tl.track(self.track_id)
        unknown = set(self.props) - {"role", "locked", "muted", "gain_db"}
        if unknown:
            raise TimelineError(f"propriétés de piste inconnues : {sorted(unknown)}")
        old = {k: getattr(t, k) for k in self.props}
        for k, v in self.props.items():
            setattr(t, k, v)
        return SetTrackProps(t.id, old)

    def describe(self):
        return f"~ piste {self.track_id} : " + ", ".join(f"{k}={v!r}" for k, v in self.props.items())


# --- repères et projet ----------------------------------------------------
@register
@dataclass
class AddMarker(Op):
    type: ClassVar[str] = "add_marker"
    marker: dict

    def apply(self, tl):
        m = Marker(**self.marker)
        if any(x.id == m.id for x in tl.markers):
            raise TimelineError(f"repère déjà existant : {m.id}")
        tl.markers.append(m)
        tl.markers.sort(key=lambda x: x.frame)
        return RemoveMarker(m.id)

    def describe(self):
        return f"+ repère {self.marker['id']} ({self.marker.get('kind', 'note')}) image {self.marker['frame']}"


@register
@dataclass
class RemoveMarker(Op):
    type: ClassVar[str] = "remove_marker"
    marker_id: str

    def apply(self, tl):
        m = tl.marker(self.marker_id)
        tl.markers.remove(m)
        return AddMarker(asdict(m))

    def describe(self):
        return f"- repère {self.marker_id}"


_PROJECT_PROPS = {"title", "source_gain_db", "music"}


@register
@dataclass
class SetProject(Op):
    type: ClassVar[str] = "set_project"
    props: dict[str, Any]

    def apply(self, tl):
        unknown = set(self.props) - _PROJECT_PROPS
        if unknown:
            raise TimelineError(f"propriétés de projet inconnues : {sorted(unknown)}")
        old = {k: getattr(tl, k) for k in self.props}
        for k, v in self.props.items():
            setattr(tl, k, v)
        return SetProject(old)

    def describe(self):
        return "~ projet : " + ", ".join(f"{k}" for k in self.props)


# --- transitions -----------------------------------------------------------
def _transition_writable(tl: Timeline, t: Transition) -> None:
    _writable(tl, tl.clip(t.from_clip))
    _writable(tl, tl.clip(t.to_clip))


@register
@dataclass
class AddTransition(Op):
    type: ClassVar[str] = "add_transition"
    transition: dict

    def apply(self, tl):
        try:
            t = Transition(**self.transition)
        except TypeError as exc:
            raise TimelineError(f"Transition mal formée : {exc}") from exc
        if any(x.id == t.id for x in tl.transitions):
            raise TimelineError(f"transition déjà existante : {t.id}")
        _transition_writable(tl, t)
        tl.transitions.append(t)
        return RemoveTransition(t.id)

    def describe(self):
        t = self.transition
        return f"+ transition {t['id']} ({t.get('type', 'fade')}, {t.get('duration', 12)} i) {t['from_clip']} → {t['to_clip']}"


@register
@dataclass
class RemoveTransition(Op):
    type: ClassVar[str] = "remove_transition"
    transition_id: str

    def apply(self, tl):
        t = tl.transition(self.transition_id)
        _transition_writable(tl, t)
        tl.transitions.remove(t)
        return AddTransition(asdict(t))

    def describe(self):
        return f"- transition {self.transition_id}"


@register
@dataclass
class SetTransition(Op):
    """Change le type, la durée (étirer/raccourcir) ou l'alignement (faire glisser) d'une transition."""
    type: ClassVar[str] = "set_transition"
    transition_id: str
    props: dict[str, Any]

    def apply(self, tl):
        t = tl.transition(self.transition_id)
        unknown = set(self.props) - {"type", "duration", "align", "params"}
        if unknown:
            raise TimelineError(f"propriétés de transition inconnues : {sorted(unknown)}")
        _transition_writable(tl, t)
        old = {k: getattr(t, k) for k in self.props}
        for k, v in self.props.items():
            setattr(t, k, v)
        return SetTransition(t.id, old)

    def describe(self):
        return f"~ transition {self.transition_id} : " + ", ".join(f"{k}={v!r}" for k, v in self.props.items())


# --- transaction ----------------------------------------------------------
@register
@dataclass
class Transaction(Op):
    """Plusieurs opérations = un seul pas d'annulation. Tout ou rien."""
    type: ClassVar[str] = "transaction"
    ops: list[Op] = field(default_factory=list)
    label: str = ""

    def apply(self, tl):
        inverses: list[Op] = []
        try:
            for op in self.ops:
                inverses.append(op.apply(tl))
        except Exception:
            for inv in reversed(inverses):
                inv.apply(tl)
            raise
        return Transaction(list(reversed(inverses)), self.label)

    def describe(self):
        return self.label or "; ".join(o.describe() for o in self.ops)

    def descriptions(self) -> list[str]:
        out: list[str] = []
        for o in self.ops:
            out.extend(o.descriptions() if isinstance(o, Transaction) else [o.describe()])
        return out

    def to_dict(self) -> dict:
        return {"type": self.type, "label": self.label, "ops": [o.to_dict() for o in self.ops]}


def execute(tl: Timeline, op: Op) -> Op:
    """Applique `op`, vérifie que la timeline reste valide, sinon revient en arrière.
    Renvoie l'inverse."""
    inverse = op.apply(tl)
    try:
        tl.validate()
    except TimelineError:
        inverse.apply(tl)
        raise
    return inverse


# --- constructeurs d'opérations composées -----------------------------------
def ripple_delete(tl: Timeline, clip_id: str) -> Transaction:
    """Supprime un plan et referme le trou sur sa piste."""
    c = tl.clip(clip_id)
    later = [x.id for x in tl.clips_on(c.track) if x.start >= c.end]
    ops: list[Op] = [RemoveClip(c.id)]
    if later:
        ops.append(ShiftClips(later, -c.duration))
    return Transaction(ops, f"- {c.id} (trou refermé)")


def trim_tail(tl: Timeline, clip_id: str, frames: int) -> TrimClip:
    """Raccourcit (frames > 0) ou rallonge (frames < 0) la fin d'un plan."""
    c = tl.clip(clip_id)
    return TrimClip(c.id, c.start, c.duration - frames, c.src_in)


def trim_head(tl: Timeline, clip_id: str, frames: int) -> TrimClip:
    """Raccourcit (frames > 0) ou rallonge (frames < 0) le début d'un plan ; la fin reste en place."""
    c = tl.clip(clip_id)
    return TrimClip(c.id, c.start + frames, c.duration - frames, c.src_in + frames / tl.fps)


def _seconds_or_frames(tl: Timeline, seconds: float | None, frames: int | None) -> int:
    if (seconds is None) == (frames is None):
        raise TimelineError("donner la durée soit en secondes, soit en images")
    n = frames if frames is not None else seconds_to_frames(seconds, tl.fps)
    if n < 1:
        raise TimelineError("durée de transition trop courte (moins d'une image)")
    return n


def add_transition(tl: Timeline, from_clip: str, type: str = "fade", *, seconds: float | None = None,
                   frames: int | None = None, align: str = "center") -> AddTransition:
    """Pose une transition sur la coupe qui suit `from_clip` (le plan suivant est déduit)."""
    a = tl.clip(from_clip)
    nxt = next((c for c in tl.clips_on(a.track) if c.start == a.end), None)
    if nxt is None:
        raise TimelineError(f"{from_clip} n'a pas de plan contigu après lui : pas de coupe à adoucir")
    if seconds is None and frames is None:             # sans durée : valeur par défaut du type
        frames = DEFAULT_TRANSITION_FRAMES.get(type, 12)
    t = Transition(tl.next_transition_id(), a.id, nxt.id, type, _seconds_or_frames(tl, seconds, frames), align)
    return AddTransition(asdict(t))


def resize_transition(tl: Timeline, transition_id: str, *, seconds: float | None = None,
                      frames: int | None = None) -> SetTransition:
    """Étire / raccourcit une transition (« mets 1 seconde de fondu » ou poignée tirée à la souris)."""
    tl.transition(transition_id)
    return SetTransition(transition_id, {"duration": _seconds_or_frames(tl, seconds, frames)})


def slide_transition(tl: Timeline, transition_id: str, align: str) -> SetTransition:
    """Fait glisser la fenêtre par rapport à la coupe : `start`, `center` ou `end`."""
    tl.transition(transition_id)
    return SetTransition(transition_id, {"align": align})
