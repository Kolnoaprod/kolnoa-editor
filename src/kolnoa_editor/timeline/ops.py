"""Opérations réversibles sur la timeline.

Chaque opération : `apply(tl)` modifie la timeline et RENVOIE l'opération inverse ;
`describe()` donne le diff lisible ; `to_dict()/op_from_dict()` la rendent journalisable.
Un `Transaction` regroupe plusieurs opérations en un seul pas d'annulation, de façon atomique.
`execute()` applique une opération, valide la timeline et revient en arrière si elle devient invalide.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict, fields
from typing import Any, ClassVar

from .model import (AUDIO_MODES, Angle, Clip, LockedError, Marker, MulticamGroup, Subclip, Timeline, TimelineError, Track)

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
        tl.clips.remove(c)
        tl.retired.append(c.id)
        return AddClip(asdict(c), restore=True)

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
                        "src_in": round(c.src_in + offset, 6), "fade_in": 0.0, "parent": c.id, "link": None,
                        "extra": dict(c.extra)})
        c.duration, c.fade_out = self.at - c.start, 0.0
        tl.clips.insert(tl.clips.index(c) + 1, right)
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
        idx = tl.clips.index(left)
        tl.clips[idx] = _clip_from(self.original)
        tl.clips.remove(right)
        tl.retired.append(right.id)
        return SplitClip(left.id, at, right.id, restore_id=True)

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


@register
@dataclass
class ShiftAfter(Op):
    """Décale de `delta` images tous les plans des pistes données qui commencent à `from_frame` ou après
    (ripple). Les plans concernés sont déterminés à l'application : l'inverse reçoit leur liste exacte."""
    type: ClassVar[str] = "shift_after"
    tracks: list[str]
    from_frame: int
    delta: int

    def apply(self, tl):
        clips = [c for t in self.tracks for c in tl.clips_on(t) if c.start >= self.from_frame]
        for c in clips:
            _writable(tl, c)
        for c in clips:
            c.start += self.delta
        return ShiftClips([c.id for c in clips], -self.delta)

    def describe(self):
        return f"~ après l'image {self.from_frame} sur {', '.join(self.tracks)} : décalage de {self.delta} i"


_CLIP_PROPS = {"audio", "fade_in", "fade_out", "gain_db", "note", "label", "locked", "extra", "media", "link"}


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


# --- groupes multicam ---------------------------------------------------------
def _group_from(d: dict) -> MulticamGroup:
    try:
        return MulticamGroup(id=d["id"], master=d["master"], name=d.get("name", ""),
                             angles=[Angle(**a) for a in d.get("angles", [])])
    except (KeyError, TypeError) as exc:
        raise TimelineError(f"Groupe multicam mal formé : {exc}") from exc


@register
@dataclass
class AddGroup(Op):
    type: ClassVar[str] = "add_group"
    group: dict

    def apply(self, tl):
        g = _group_from(self.group)
        if any(x.id == g.id for x in tl.groups):
            raise TimelineError(f"groupe déjà existant : {g.id}")
        tl.groups.append(g)
        return RemoveGroup(g.id)

    def describe(self):
        return f"+ groupe multicam {self.group['id']} ({len(self.group.get('angles', []))} angle(s))"


@register
@dataclass
class RemoveGroup(Op):
    type: ClassVar[str] = "remove_group"
    group_id: str

    def apply(self, tl):
        g = tl.group(self.group_id)
        users = [c.id for c in tl.clips if c.group == g.id]
        if users:
            raise TimelineError(f"le groupe {g.id} est utilisé par {', '.join(users)}")
        tl.groups.remove(g)
        return AddGroup(asdict(g))

    def describe(self):
        return f"- groupe multicam {self.group_id}"


@register
@dataclass
class AddAngle(Op):
    type: ClassVar[str] = "add_angle"
    group_id: str
    angle: dict

    def apply(self, tl):
        g = tl.group(self.group_id)
        try:
            a = Angle(**self.angle)
        except TypeError as exc:
            raise TimelineError(f"Angle mal formé : {exc}") from exc
        if any(x.id == a.id for x in g.angles):
            raise TimelineError(f"angle déjà existant : {g.id}/{a.id}")
        g.angles.append(a)
        return RemoveAngle(g.id, a.id)

    def describe(self):
        return f"+ angle {self.group_id}/{self.angle['id']} ({self.angle['media']})"


@register
@dataclass
class RemoveAngle(Op):
    type: ClassVar[str] = "remove_angle"
    group_id: str
    angle_id: str

    def apply(self, tl):
        g = tl.group(self.group_id)
        a = g.angle(self.angle_id)
        if a.id == g.master:
            raise TimelineError(f"{a.id} est l'angle maître du groupe {g.id}")
        if a.locked:
            raise LockedError(f"l'angle {a.id} est verrouillé")
        users = [c.id for c in tl.clips if c.group == g.id and c.angle == a.id]
        if users:
            raise TimelineError(f"l'angle {a.id} est utilisé par {', '.join(users)}")
        g.angles.remove(a)
        return AddAngle(g.id, asdict(a))

    def describe(self):
        return f"- angle {self.group_id}/{self.angle_id}"


_ANGLE_PROPS = {"offset", "drift_ppm", "confidence", "status", "method", "locked", "points", "range", "note",
                "has_audio"}


@register
@dataclass
class SetAngleSync(Op):
    """Règle la synchro d'un angle (calcul automatique, alignement manuel, verrou). Un angle verrouillé refuse
    toute modification de synchro ; on peut toujours le déverrouiller. L'angle maître définit l'axe du groupe."""
    type: ClassVar[str] = "set_angle_sync"
    group_id: str
    angle_id: str
    props: dict[str, Any]

    def apply(self, tl):
        g = tl.group(self.group_id)
        a = g.angle(self.angle_id)
        unknown = set(self.props) - _ANGLE_PROPS
        if unknown:
            raise TimelineError(f"propriétés d'angle inconnues : {sorted(unknown)}")
        if a.locked and set(self.props) != {"locked"}:
            raise LockedError(f"l'angle {g.id}/{a.id} est verrouillé")
        if a.id == g.master and set(self.props) & {"offset", "drift_ppm", "status", "method"}:
            raise TimelineError(f"{a.id} est l'angle maître : il définit l'axe du groupe")
        old = {k: copy.deepcopy(getattr(a, k)) for k in self.props}
        for k, v in self.props.items():
            setattr(a, k, copy.deepcopy(v))
        return SetAngleSync(g.id, a.id, old)

    def describe(self):
        return f"~ synchro {self.group_id}/{self.angle_id} : " + ", ".join(
            f"{k}={v!r}" for k, v in self.props.items() if k != "points")


@register
@dataclass
class SetClipAngle(Op):
    """Change l'angle montré par un plan multicam. Bas niveau : voir `switch_angle` pour le calcul du point d'entrée."""
    type: ClassVar[str] = "set_clip_angle"
    clip_id: str
    angle: str
    media: str
    src_in: float

    def apply(self, tl):
        c = tl.clip(self.clip_id)
        if not c.group:
            raise TimelineError(f"{c.id} n'est pas un plan multicam")
        _writable(tl, c)
        tl.group(c.group).angle(self.angle)
        old = SetClipAngle(c.id, c.angle or "", c.media, c.src_in)
        c.angle, c.media, c.src_in = self.angle, self.media, round(self.src_in, 6)
        return old

    def describe(self):
        return f"~ {self.clip_id} : angle {self.angle}"


# --- chutier --------------------------------------------------------------------
def _sub_from(d: dict) -> Subclip:
    try:
        return Subclip(**d)
    except TypeError as exc:
        raise TimelineError(f"Plan de chutier mal formé : {exc}") from exc


@register
@dataclass
class AddSubclip(Op):
    type: ClassVar[str] = "add_subclip"
    sub: dict

    def apply(self, tl):
        b = _sub_from(self.sub)
        if any(x.id == b.id for x in tl.bin):
            raise TimelineError(f"plan de chutier déjà existant : {b.id}")
        tl.bin.append(b)
        return RemoveSubclip(b.id)

    def describe(self):
        return f"+ chutier {self.sub.get('id')} ({self.sub.get('media')})"


@register
@dataclass
class RemoveSubclip(Op):
    type: ClassVar[str] = "remove_subclip"
    sub_id: str

    def apply(self, tl):
        b = tl.subclip(self.sub_id)
        tl.bin.remove(b)
        return AddSubclip(asdict(b))

    def describe(self):
        return f"- chutier {self.sub_id}"


@register
@dataclass
class SetSubclip(Op):
    """Modifie le nom, les repères I/O, les étiquettes ou la note d'un plan du chutier."""
    type: ClassVar[str] = "set_subclip"
    sub_id: str
    props: dict

    def apply(self, tl):
        b = tl.subclip(self.sub_id)
        allowed = {"name", "src_in", "src_out", "tags", "note"}
        bad = set(self.props) - allowed
        if bad:
            raise TimelineError(f"propriétés de chutier inconnues : {', '.join(sorted(bad))}")
        old = {k: copy.deepcopy(getattr(b, k)) for k in self.props}
        for k, v in self.props.items():
            setattr(b, k, copy.deepcopy(v))
        return SetSubclip(b.id, old)

    def describe(self):
        return f"~ chutier {self.sub_id} : {', '.join(self.props)}"


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


# --- constructeurs multicam ---------------------------------------------------------
def _usable_angle(tl: Timeline, group_id: str, angle_id: str):
    g = tl.group(group_id)
    a = g.angle(angle_id)
    if not a.usable:
        raise TimelineError(f"l'angle {g.id}/{a.id} n'est pas synchronisé de façon fiable (état « {a.status} ») : "
                            "valider ou aligner à la main d'abord")
    return g, a


def add_multicam_clip(tl: Timeline, group_id: str, angle_id: str, track: str, start: int, duration: int,
                      group_in: float, clip_id: str | None = None) -> AddClip:
    """Plan montrant `angle_id` à partir de l'instant `group_in` (secondes de l'axe du groupe) ; son = maître."""
    g, a = _usable_angle(tl, group_id, angle_id)
    cid = clip_id or tl.next_clip_id()
    c = Clip(id=cid, track=track, media=a.media, start=start, duration=duration,
             src_in=round(a.to_angle(group_in), 6), audio="group", group=g.id, angle=a.id)
    return AddClip(asdict(c))


def switch_angle(tl: Timeline, clip_id: str, angle_id: str) -> SetClipAngle:
    """Change d'angle en gardant le MÊME instant de groupe : l'image change, le son maître ne bouge pas."""
    c = tl.clip(clip_id)
    if not c.group:
        raise TimelineError(f"{c.id} n'est pas un plan multicam")
    g, new = _usable_angle(tl, c.group, angle_id)
    old = g.angle(c.angle or "")
    t_group = old.to_group(c.src_in)
    return SetClipAngle(c.id, new.id, new.media, new.to_angle(t_group))


def nudge_angle(tl: Timeline, group_id: str, angle_id: str, frames: int) -> SetAngleSync:
    """Réglage image par image (cadence du projet) : avance (+) ou recule (−) l'angle sur l'axe du groupe."""
    a = tl.group(group_id).angle(angle_id)
    return SetAngleSync(group_id, angle_id, {
        "offset": round(a.offset + frames / tl.fps, 6), "method": "manual", "status": "manual", "confidence": 1.0})


def lock_angle(tl: Timeline, group_id: str, angle_id: str, locked: bool = True) -> SetAngleSync:
    tl.group(group_id).angle(angle_id)
    return SetAngleSync(group_id, angle_id, {"locked": locked})


def accept_angle(tl: Timeline, group_id: str, angle_id: str) -> SetAngleSync:
    """Validation humaine d'une proposition incertaine (ou visuelle) : elle devient utilisable (état « manual »)."""
    a = tl.group(group_id).angle(angle_id)
    if a.status in ("impossible", "unsynced", "reference"):
        raise TimelineError(f"rien à valider : l'angle {angle_id} est « {a.status} »")
    return SetAngleSync(group_id, angle_id, {"status": "manual", "confidence": 1.0})
