"""La liste de décisions : le montage décrit en données (JSON), rendu ensuite par ffmpeg.

C'est le format d'échange avec l'IA (Claude / ChatGPT) : elle lit et écrit ce fichier,
Kolnoa le rend. Chaque segment a un id stable (s001, s002...) pour que les retours restent
valables d'une version à l'autre.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
import json
import re

SCHEMA = 1
AUDIO_MODES = {"source", "mute"}
MIN_PIECE = 0.04  # une image à 25 i/s : plus petit morceau acceptable lors d'une scission
_ID_RE = re.compile(r"^s(\d+)$")


class DecisionListError(ValueError):
    pass


@dataclass
class Segment:
    id: str
    clip: str                 # chemin relatif au dossier mariage (séparateurs '/')
    start: float              # secondes dans le clip source
    end: float
    audio: str = "source"     # 'source' (son du clip) ou 'mute'
    fade_in: float = 0.0      # secondes (fondu vidéo + audio)
    fade_out: float = 0.0
    note: str = ""            # intention de montage, lisible par l'humain et l'IA
    parent: str | None = None  # filiation : id du segment dont celui-ci est issu (scission)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Music:
    path: str                 # relatif au dossier mariage
    start: float = 0.0        # décalage dans le fichier musique
    gain_db: float = 0.0
    fade_out: float = 2.0


@dataclass
class DecisionList:
    title: str
    version: int = 1
    fps: int = 25
    resolution: tuple[int, int] = (1920, 1080)
    source_gain_db: float = 0.0       # gain appliqué au son des clips (utile sous la musique)
    music: Music | None = None
    segments: list[Segment] = field(default_factory=list)
    schema: int = SCHEMA
    retired: list[str] = field(default_factory=list)  # ids supprimés : jamais réutilisés

    # --- édition avec filiation d'ids ---------------------------------
    def _all_ids(self) -> set[str]:
        ids = {s.id for s in self.segments} | set(self.retired)
        ids |= {s.parent for s in self.segments if s.parent}
        return ids

    def next_id(self) -> str:
        """Prochain id libre (s001, s002...), jamais un id déjà utilisé ou supprimé."""
        nums = [int(m.group(1)) for i in self._all_ids() if (m := _ID_RE.match(i))]
        return f"s{max(nums, default=0) + 1:03d}"

    def get(self, segment_id: str) -> Segment:
        for s in self.segments:
            if s.id == segment_id:
                return s
        raise DecisionListError(f"Segment inconnu : {segment_id}")

    def split(self, segment_id: str, at: float, new_id: str | None = None) -> str:
        """Scinde un segment au temps source `at`. La 1re partie garde l'id (les retours qui la
        concernent restent valables) ; la 2de reçoit un nouvel id et `parent` = id d'origine."""
        seg = self.get(segment_id)
        if not (seg.start + MIN_PIECE <= at <= seg.end - MIN_PIECE):
            raise DecisionListError(
                f"{segment_id}: scission à {at:.2f}s impossible (plage {seg.start:.2f}-{seg.end:.2f}s)"
            )
        new_id = new_id or self.next_id()
        if new_id in self._all_ids():
            raise DecisionListError(f"id déjà utilisé : {new_id}")
        right = replace(seg, id=new_id, start=at, fade_in=0.0, parent=seg.id)
        seg.end, seg.fade_out = at, 0.0
        self.segments.insert(self.segments.index(seg) + 1, right)
        return new_id

    def remove(self, segment_id: str) -> None:
        seg = self.get(segment_id)
        self.segments.remove(seg)
        if segment_id not in self.retired:
            self.retired.append(segment_id)

    def insert(self, seg: Segment, index: int | None = None) -> None:
        if seg.id in self._all_ids():
            raise DecisionListError(f"id déjà utilisé ou supprimé : {seg.id}")
        self.segments.insert(len(self.segments) if index is None else index, seg)

    def descends_from(self, segment_id: str, ancestor_id: str) -> bool:
        by_id = {s.id: s for s in self.segments}
        cur, seen = by_id.get(segment_id), set()
        while cur is not None and cur.parent and cur.id not in seen:
            seen.add(cur.id)
            if cur.parent == ancestor_id:
                return True
            cur = by_id.get(cur.parent)
        return False

    # --- calculs ------------------------------------------------------
    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    def locate(self, t: float) -> tuple[Segment, float]:
        """Segment visible à l'instant t du rendu, et position (s) dans ce segment."""
        if not self.segments:
            raise DecisionListError("Liste de décisions vide")
        if t < 0 or t > self.duration + 1e-6:
            raise DecisionListError(f"Instant {t:.2f}s hors du film (durée {self.duration:.2f}s)")
        acc = 0.0
        for seg in self.segments:
            if t < acc + seg.duration - 1e-9:
                return seg, t - acc
            acc += seg.duration
        last = self.segments[-1]
        return last, last.duration

    def start_of(self, segment_id: str) -> float:
        acc = 0.0
        for seg in self.segments:
            if seg.id == segment_id:
                return acc
            acc += seg.duration
        raise DecisionListError(f"Segment inconnu : {segment_id}")

    # --- validation ---------------------------------------------------
    def validate(self, project=None) -> None:
        if self.schema != SCHEMA:
            raise DecisionListError(f"Schéma {self.schema} non supporté (attendu {SCHEMA})")
        if self.fps <= 0:
            raise DecisionListError("fps invalide")
        if len(self.resolution) != 2 or min(self.resolution) <= 0:
            raise DecisionListError("résolution invalide")
        seen: set[str] = set()
        for seg in self.segments:
            if not seg.id or seg.id in seen:
                raise DecisionListError(f"id de segment vide ou en double : {seg.id!r}")
            seen.add(seg.id)
            if seg.id in self.retired:
                raise DecisionListError(f"{seg.id}: id supprimé précédemment, il ne peut pas être réutilisé")
            if seg.start < 0 or seg.end <= seg.start:
                raise DecisionListError(f"{seg.id}: plage invalide ({seg.start} -> {seg.end})")
            if seg.audio not in AUDIO_MODES:
                raise DecisionListError(f"{seg.id}: audio doit être 'source' ou 'mute'")
            if seg.fade_in < 0 or seg.fade_out < 0 or seg.fade_in + seg.fade_out > seg.duration:
                raise DecisionListError(f"{seg.id}: fondus invalides")
            if project is not None:
                project.resolve(seg.clip)  # refuse les chemins hors projet
                if not project.resolve(seg.clip).is_file():
                    raise DecisionListError(f"{seg.id}: clip introuvable : {seg.clip}")
        if self.music is not None and project is not None:
            if not project.resolve(self.music.path).is_file():
                raise DecisionListError(f"musique introuvable : {self.music.path}")

    # --- (dé)sérialisation -------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["resolution"] = list(self.resolution)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionList":
        try:
            music = Music(**d["music"]) if d.get("music") else None
            segments = [Segment(**s) for s in d.get("segments", [])]
            return cls(
                title=d["title"],
                version=int(d.get("version", 1)),
                fps=int(d.get("fps", 25)),
                resolution=tuple(d.get("resolution", (1920, 1080))),  # type: ignore[arg-type]
                source_gain_db=float(d.get("source_gain_db", 0.0)),
                music=music,
                segments=segments,
                schema=int(d.get("schema", SCHEMA)),
                retired=list(d.get("retired", [])),
            )
        except (KeyError, TypeError) as exc:
            raise DecisionListError(f"Liste de décisions mal formée : {exc}") from exc

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "DecisionList":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def diff(old: DecisionList, new: DecisionList) -> list[str]:
    """Différences lisibles entre deux versions (avant/après), segment par segment."""
    lines: list[str] = []
    old_by = {s.id: s for s in old.segments}
    new_by = {s.id: s for s in new.segments}
    for sid, s in new_by.items():
        if sid not in old_by:
            origin = f" (issu de {s.parent})" if s.parent else ""
            lines.append(f"+ {sid}{origin} : {s.clip} {s.start:.2f}-{s.end:.2f}s")
    for sid, s in old_by.items():
        if sid not in new_by:
            lines.append(f"- {sid} : {s.clip} {s.start:.2f}-{s.end:.2f}s")
    for sid, n in new_by.items():
        o = old_by.get(sid)
        if o is None:
            continue
        changes = []
        if o.clip != n.clip:
            changes.append(f"clip {o.clip} → {n.clip}")
        if (o.start, o.end) != (n.start, n.end):
            changes.append(f"plage {o.start:.2f}-{o.end:.2f}s → {n.start:.2f}-{n.end:.2f}s")
        for attr in ("audio", "fade_in", "fade_out"):
            if getattr(o, attr) != getattr(n, attr):
                changes.append(f"{attr} {getattr(o, attr)} → {getattr(n, attr)}")
        if changes:
            lines.append(f"~ {sid} : " + " ; ".join(changes))
    common_old = [s.id for s in old.segments if s.id in new_by]
    common_new = [s.id for s in new.segments if s.id in old_by]
    if common_old != common_new:
        lines.append("~ ordre des segments modifié")
    if old.music != new.music:
        lines.append("~ musique modifiée")
    if abs(old.duration - new.duration) > 1e-6:
        lines.append(f"= durée {old.duration:.1f}s → {new.duration:.1f}s")
    return lines
