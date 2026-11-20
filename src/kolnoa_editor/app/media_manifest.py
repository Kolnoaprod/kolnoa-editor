"""Inventaire de références externes d'un projet .klnprj."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from uuid import UUID, uuid4
from datetime import datetime

from ..ffmpeg import CancelToken
from ..auto.analysis import RushInfo
from ..probe import probe
from ..scanner import AUDIO_EXT, VIDEO_EXT
from ..timeline import Session, Timeline
from ..timeline.model import Track

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


@dataclass(frozen=True)
class MediaReference:
    id: str
    path: str
    kind: str
    title: str
    duration: float | None = None
    has_audio: bool = False
    start_time: str | None = None


@dataclass(frozen=True)
class EditorialSequence:
    id: str
    title: str


class MediaManifest:
    def __init__(self, path: Path):
        self.path = path
        self.media: list[MediaReference] = []
        self.sequences: list[EditorialSequence] = []
        self.active_sequence: str | None = None
        self.root: str | None = None
        self.data_dir: str | None = None
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw["version"] != 1:
                raise ValueError("Version de projet .klnprj inconnue")
            self.media = [MediaReference(**item) for item in raw["media"]]
            self.sequences = [EditorialSequence(**item) for item in raw.get("sequences", [])]
            if any(UUID(seq.id).hex != seq.id for seq in self.sequences):
                raise ValueError("Identifiant de séquence invalide")
            self.active_sequence = raw.get("active_sequence")
            if self.active_sequence is not None and self.active_sequence not in {seq.id for seq in self.sequences}:
                raise ValueError("Séquence active inconnue")
            self.root = raw.get("root")
            self.data_dir = raw.get("data_dir")

    def save(self, *, media=None):
        content = {
            "version": 1, "media": [asdict(ref) for ref in (self.media if media is None else media)],
            "root": self.root, "data_dir": self.data_dir,
            "sequences": [asdict(sequence) for sequence in self.sequences], "active_sequence": self.active_sequence,
        }
        tmp = self.path.with_suffix(f".{uuid4().hex}.part")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def bind(self, controller):
        self.root = str(controller.project.root.resolve())
        self.data_dir = str(controller.project.data.resolve())

    def add_video_references(self, controller):
        root = controller.project.root.resolve()
        for ref in self.media:
            if ref.kind != "video" or not ref.duration:
                continue
            path = Path(ref.path)
            media = (path.relative_to(root) if path.is_relative_to(root) else path).as_posix()
            controller.rushes.setdefault(media, RushInfo(media, ref.duration, ref.start_time,
                                                         group=path.parent.name, has_audio=ref.has_audio))

    def create_sequence(self, controller, title: str) -> EditorialSequence:
        title = title.strip()
        if not title or any(item.title.casefold() == title.casefold() for item in self.sequences):
            raise ValueError("Donnez un nom de séquence unique.")
        self.bind(controller)
        sequence = EditorialSequence(uuid4().hex, title)
        directory = Path(self.data_dir) / "sequences" / sequence.id
        timeline = Timeline(title=title, fps=controller.tl.fps, resolution=controller.tl.resolution,
                            tracks=[Track("V1", "video", "main"), Track("A1", "audio", "main")])
        Session.create(directory, timeline)
        self.sequences.append(sequence)
        try:
            self.save()
        except OSError:
            self.sequences.pop()
            raise
        return sequence

    def open_sequence(self, controller, sequence_id: str | None):
        if sequence_id is None:
            path = controller.project.timeline_dir
        else:
            sequence = next(item for item in self.sequences if item.id == sequence_id)
            path = controller.project.data / "sequences" / sequence.id
        session = Session.open(path)
        previous = self.active_sequence
        self.active_sequence = sequence_id
        try:
            self.save()
        except OSError:
            self.active_sequence = previous
            raise
        return session

    def import_paths(self, paths, *, recursive=False, music=False, cancel=None, progress=None):
        token = cancel or CancelToken()
        known = {os.path.normcase(ref.path) for ref in self.media}
        pending = []
        for path in paths:
            token.raise_if_cancelled()
            source = Path(path)
            if not source.exists():
                raise FileNotFoundError(source)
            candidates = (source.rglob("*") if recursive else source.iterdir()) if source.is_dir() else (source,)
            for item in candidates:
                token.raise_if_cancelled()
                ext = item.suffix.lower()
                if not item.is_file():
                    continue
                if ext in AUDIO_EXT:
                    kind = "music" if music else "audio"
                elif not music and ext in VIDEO_EXT:
                    kind = "video"
                elif not music and ext in IMAGE_EXT:
                    kind = "image"
                else:
                    continue
                resolved = str(item.resolve())
                key = os.path.normcase(resolved)
                if key not in known:
                    pending.append((resolved, kind))
                    known.add(key)

        added = []
        for i, (path, kind) in enumerate(sorted(pending)):
            if progress:
                progress("Lecture des métadonnées", path, i, len(pending), path)
            token.raise_if_cancelled()
            info = probe(path, cancel=token) if kind != "image" else None
            stamp = datetime.fromtimestamp(Path(path).stat().st_mtime).isoformat(timespec="seconds")
            added.append(MediaReference(uuid4().hex, path, kind, Path(path).stem,
                                        info.duration if info else None, bool(info and info.has_audio), stamp))
        token.raise_if_cancelled()
        if added:
            self.save(media=self.media + added)
            self.media.extend(added)
        return added
