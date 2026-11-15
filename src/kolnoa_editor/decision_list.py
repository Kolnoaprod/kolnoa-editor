"""La liste de décisions : le montage décrit en données (JSON), rendu ensuite par ffmpeg.

C'est le format d'échange avec l'IA (Claude / ChatGPT) : elle lit et écrit ce fichier,
Kolnoa le rend. Chaque segment a un id stable (s001, s002...) pour que les retours restent
valables d'une version à l'autre.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
import json

SCHEMA = 1
AUDIO_MODES = {"source", "mute"}


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
            )
        except (KeyError, TypeError) as exc:
            raise DecisionListError(f"Liste de décisions mal formée : {exc}") from exc

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "DecisionList":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
