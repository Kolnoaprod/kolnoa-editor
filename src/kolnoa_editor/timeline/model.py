"""Modèle canonique de la timeline Kolnoa (schéma 1).

Positions et durées de timeline : images entières à la cadence du projet. Temps source et
fondus : secondes (le son et les fondus ne sont pas quantifiés à l'image).
Les ids des plans gardent la forme `s001` de la liste de décisions : les retours, versions et
filiations déjà en place restent valables. Un id supprimé n'est jamais réutilisé (`retired`).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, asdict
from typing import Any

SCHEMA = 1
TRACK_KINDS = {"video", "audio"}
AUDIO_MODES = {"source", "mute"}
MARKER_KINDS = {"note", "beat", "phrase", "sync", "chapter", "keep"}  # keep = zone à ne pas couper
TRANSITION_TYPES = {"fade", "fadeblack", "fadewhite", "flash"}   # catalogue simple (docs/TRANSITIONS.md)
TRANSITION_ALIGN = {"center", "start", "end"}   # position de la fenêtre par rapport à la coupe
_TRANSITION_ID = re.compile(r"^t(\d+)$")
_CLIP_ID = re.compile(r"^s(\d+)$")
_MARKER_ID = re.compile(r"^m(\d+)$")


class TimelineError(ValueError):
    """Opération ou état de timeline invalide."""


class LockedError(TimelineError):
    """Tentative de modifier un plan ou une piste verrouillés."""


@dataclass
class Track:
    id: str
    kind: str = "video"          # 'video' | 'audio'
    role: str = "main"           # main, ambiance, voix, musique, effets...
    locked: bool = False
    muted: bool = False
    gain_db: float = 0.0


@dataclass
class Clip:
    id: str
    track: str
    media: str                   # chemin relatif au dossier mariage ('/')
    start: int                   # image de timeline
    duration: int                # images
    src_in: float = 0.0          # secondes dans le média
    audio: str = "source"
    fade_in: float = 0.0         # secondes
    fade_out: float = 0.0
    gain_db: float = 0.0
    note: str = ""               # intention de montage
    label: str = ""
    locked: bool = False
    parent: str | None = None    # filiation (scission)
    extra: dict[str, Any] = field(default_factory=dict)  # réservé (transitions, effets, étalonnage)

    @property
    def end(self) -> int:
        return self.start + self.duration


@dataclass
class Marker:
    id: str
    frame: int
    kind: str = "note"
    text: str = ""


@dataclass
class Transition:
    """Transition posée SUR une coupe (fin du plan `from_clip` = début du plan `to_clip`).

    La fenêtre dure `duration` images et ne change jamais la durée du film : `center` l'étale de part et
    d'autre de la coupe, `start` la fait commencer à la coupe, `end` la fait finir sur la coupe. On l'étire
    ou la raccourcit en changeant `duration`, on la fait glisser en changeant `align`.
    """
    id: str
    from_clip: str
    to_clip: str
    type: str = "fade"
    duration: int = 12           # images
    align: str = "center"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Timeline:
    title: str
    version: int = 1
    fps: int = 25
    resolution: tuple[int, int] = (1920, 1080)
    source_gain_db: float = 0.0
    music: dict[str, Any] | None = None     # {path, start, gain_db, fade_out}
    tracks: list[Track] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    schema: int = SCHEMA

    # --- accès ---------------------------------------------------------
    def track(self, track_id: str) -> Track:
        for t in self.tracks:
            if t.id == track_id:
                return t
        raise TimelineError(f"Piste inconnue : {track_id}")

    def clip(self, clip_id: str) -> Clip:
        for c in self.clips:
            if c.id == clip_id:
                return c
        raise TimelineError(f"Plan inconnu : {clip_id}")

    def marker(self, marker_id: str) -> Marker:
        for m in self.markers:
            if m.id == marker_id:
                return m
        raise TimelineError(f"Repère inconnu : {marker_id}")

    def transition(self, transition_id: str) -> Transition:
        for t in self.transitions:
            if t.id == transition_id:
                return t
        raise TimelineError(f"Transition inconnue : {transition_id}")

    def transition_at(self, from_clip: str, to_clip: str) -> Transition | None:
        return next((t for t in self.transitions if t.from_clip == from_clip and t.to_clip == to_clip), None)

    def transitions_of(self, clip_id: str) -> list[Transition]:
        return [t for t in self.transitions if clip_id in (t.from_clip, t.to_clip)]

    def window(self, t: Transition) -> tuple[int, int]:
        """Fenêtre [début, fin[ de la transition sur la timeline, en images."""
        cut = self.clip(t.to_clip).start
        before = {"center": t.duration // 2, "start": 0, "end": t.duration}[t.align]
        return cut - before, cut - before + t.duration

    def clips_on(self, track_id: str) -> list[Clip]:
        return sorted((c for c in self.clips if c.track == track_id), key=lambda c: c.start)

    @property
    def end_frame(self) -> int:
        return max((c.end for c in self.clips), default=0)

    @property
    def duration(self) -> float:
        return self.end_frame / self.fps

    # --- ids ------------------------------------------------------------
    def _used_clip_ids(self) -> set[str]:
        ids = {c.id for c in self.clips} | set(self.retired)
        ids |= {c.parent for c in self.clips if c.parent}
        return ids

    def next_clip_id(self) -> str:
        nums = [int(m.group(1)) for i in self._used_clip_ids() if (m := _CLIP_ID.match(i))]
        return f"s{max(nums, default=0) + 1:03d}"

    def next_transition_id(self) -> str:
        nums = [int(m.group(1)) for t in self.transitions if (m := _TRANSITION_ID.match(t.id))]
        return f"t{max(nums, default=0) + 1:03d}"

    def next_marker_id(self) -> str:
        nums = [int(m.group(1)) for mk in self.markers if (m := _MARKER_ID.match(mk.id))]
        return f"m{max(nums, default=0) + 1:03d}"

    def descends_from(self, clip_id: str, ancestor_id: str) -> bool:
        by_id = {c.id: c for c in self.clips}
        cur, seen = by_id.get(clip_id), set()
        while cur is not None and cur.parent and cur.id not in seen:
            seen.add(cur.id)
            if cur.parent == ancestor_id:
                return True
            cur = by_id.get(cur.parent)
        return False

    # --- validation -----------------------------------------------------
    def validate(self) -> None:
        if self.schema != SCHEMA:
            raise TimelineError(f"Schéma de timeline {self.schema} non supporté (attendu {SCHEMA})")
        if self.fps <= 0 or len(self.resolution) != 2 or min(self.resolution) <= 0:
            raise TimelineError("cadence ou résolution invalide")
        track_ids: set[str] = set()
        for t in self.tracks:
            if not t.id or t.id in track_ids:
                raise TimelineError(f"id de piste vide ou en double : {t.id!r}")
            if t.kind not in TRACK_KINDS:
                raise TimelineError(f"{t.id}: type de piste invalide : {t.kind!r}")
            track_ids.add(t.id)
        seen: set[str] = set()
        for c in self.clips:
            if not c.id or c.id in seen:
                raise TimelineError(f"id de plan vide ou en double : {c.id!r}")
            seen.add(c.id)
            if c.id in self.retired:
                raise TimelineError(f"{c.id}: id supprimé précédemment, il ne peut pas être réutilisé")
            if c.track not in track_ids:
                raise TimelineError(f"{c.id}: piste inconnue {c.track!r}")
            if not isinstance(c.start, int) or not isinstance(c.duration, int):
                raise TimelineError(f"{c.id}: début et durée doivent être en images entières")
            if c.start < 0 or c.duration <= 0:
                raise TimelineError(f"{c.id}: position invalide (début {c.start}, durée {c.duration} images)")
            if c.src_in < 0:
                raise TimelineError(f"{c.id}: point d'entrée source négatif")
            if c.audio not in AUDIO_MODES:
                raise TimelineError(f"{c.id}: audio doit être 'source' ou 'mute'")
            if c.fade_in < 0 or c.fade_out < 0 or c.fade_in + c.fade_out > c.duration / self.fps + 1e-6:
                raise TimelineError(f"{c.id}: fondus invalides")
        for tid in track_ids:
            prev: Clip | None = None
            for c in self.clips_on(tid):
                if prev is not None and c.start < prev.end:
                    raise TimelineError(f"chevauchement sur {tid} : {prev.id} et {c.id}")
                prev = c
        self._validate_transitions()
        mseen: set[str] = set()
        for m in self.markers:
            if m.id in mseen:
                raise TimelineError(f"id de repère en double : {m.id}")
            mseen.add(m.id)
            if m.frame < 0 or m.kind not in MARKER_KINDS:
                raise TimelineError(f"{m.id}: repère invalide")

    def _validate_transitions(self) -> None:
        seen: set[str] = set()
        cuts: set[tuple[str, str]] = set()
        used: dict[str, int] = {}   # images de chaque plan mobilisées par ses transitions
        for t in self.transitions:
            if t.id in seen:
                raise TimelineError(f"id de transition en double : {t.id}")
            seen.add(t.id)
            if t.type not in TRANSITION_TYPES or t.align not in TRANSITION_ALIGN:
                raise TimelineError(f"{t.id}: type ou alignement inconnu ({t.type!r}, {t.align!r})")
            if not isinstance(t.duration, int) or t.duration < 1:
                raise TimelineError(f"{t.id}: durée invalide ({t.duration!r} images)")
            try:
                a, b = self.clip(t.from_clip), self.clip(t.to_clip)
            except TimelineError:
                raise TimelineError(f"{t.id}: plan inconnu ({t.from_clip} -> {t.to_clip})") from None
            if a.track != b.track or a.end != b.start:
                raise TimelineError(f"{t.id}: {t.from_clip} et {t.to_clip} ne sont pas contigus "
                                    "(retirer la transition avant de modifier la coupe)")
            if (t.from_clip, t.to_clip) in cuts:
                raise TimelineError(f"{t.id}: une transition existe déjà sur cette coupe")
            cuts.add((t.from_clip, t.to_clip))
            limit = min(a.duration, b.duration) // 2
            if t.duration > limit:
                raise TimelineError(f"{t.id}: {t.duration} images dépasse la moitié du plus court des deux plans "
                                    f"(maximum {limit} images)")
            before = {"center": t.duration // 2, "start": 0, "end": t.duration}[t.align]
            used[a.id] = used.get(a.id, 0) + before
            used[b.id] = used.get(b.id, 0) + (t.duration - before)
        for cid, n in used.items():
            if n > self.clip(cid).duration:
                raise TimelineError(f"{cid}: ses transitions se chevauchent ({n} images pour "
                                    f"{self.clip(cid).duration})")

    # --- (dé)sérialisation ---------------------------------------------
    def copy(self) -> "Timeline":
        return copy.deepcopy(self)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["resolution"] = list(self.resolution)
        return d

    def canonical(self) -> dict:
        """Forme normalisée (ordre indépendant de l'historique des opérations) pour comparer deux états."""
        d = self.to_dict()
        d["clips"] = sorted(d["clips"], key=lambda c: (c["track"], c["start"], c["id"]))
        d["markers"] = sorted(d["markers"], key=lambda m: (m["frame"], m["id"]))
        d["transitions"] = sorted(d["transitions"], key=lambda t: t["id"])
        d["retired"] = sorted(d["retired"])
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        d = migrate(d)
        try:
            return cls(
                title=d["title"], version=int(d.get("version", 1)), fps=int(d.get("fps", 25)),
                resolution=tuple(d.get("resolution", (1920, 1080))),  # type: ignore[arg-type]
                source_gain_db=float(d.get("source_gain_db", 0.0)),
                music=d.get("music"),
                tracks=[Track(**t) for t in d.get("tracks", [])],
                clips=[Clip(**c) for c in d.get("clips", [])],
                markers=[Marker(**m) for m in d.get("markers", [])],
                transitions=[Transition(**t) for t in d.get("transitions", [])],
                retired=list(d.get("retired", [])), schema=int(d["schema"]),
            )
        except (KeyError, TypeError) as exc:
            raise TimelineError(f"Timeline mal formée : {exc}") from exc


def migrate(d: dict) -> dict:
    """Met un dictionnaire de timeline au schéma courant (chaîne de migrations testée).

    Schéma 1 = schéma courant : seule l'absence de numéro est rejetée. Les futures versions
    ajouteront ici `_migrate_1_to_2`, etc., sans jamais modifier le fichier d'origine.
    """
    if not isinstance(d, dict) or "schema" not in d:
        raise TimelineError("Timeline sans numéro de schéma")
    version = int(d["schema"])
    if version > SCHEMA:
        raise TimelineError(f"Timeline de schéma {version} : ce Kolnoa ne connaît que le schéma {SCHEMA}")
    d = copy.deepcopy(d)
    while version < SCHEMA:
        step = _MIGRATIONS.get(version)
        if step is None:
            raise TimelineError(f"Aucune migration depuis le schéma {version}")
        d = step(d)
        version = int(d["schema"])
    return d


# version -> fonction qui renvoie le dictionnaire au schéma version+1 (vide tant que le schéma 1 est courant)
_MIGRATIONS: dict[int, Any] = {}
