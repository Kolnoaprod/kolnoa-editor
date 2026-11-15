"""Scan d'un dossier de mariage : classe chaque fichier (caméra, drone, audio) et date chaque prise.

Les dates servent à placer les plans dans le temps, notamment le drone qui n'a pas de son.
Sources de date, par ordre de fiabilité :
  - DJI   : DJI_AAAAMMJJHHMMSS_xxxx_D.MP4 (heure de l'horloge du drone)
  - Canon : A_xxxxCnnnAyymmdd_hhmmss..._CANON.MP4
  - Sony  : AAAAMMJJ_Cnnnn.MP4 (date seulement) -> l'heure vient de la date de modification
  - sinon : date de modification du fichier
Chaque appareil a sa propre horloge : un décalage par appareil est à calibrer (voir sync).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from .project import Project

VIDEO_EXT = {".mp4", ".mov", ".mxf", ".mts", ".m4v"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".aif", ".aiff"}

# Noms de dossiers reconnus (comparaison en majuscules, sans espaces). Ordre = priorité.
DEVICE_FOLDERS = ["A7IV", "OP4P", "OP4", "DRONE", "CANON", "AUDIO", "MUSIQUE"]

RE_DJI = re.compile(r"DJI_(\d{14})_\d{4}_[A-Z]", re.I)
RE_CANON = re.compile(r"A(\d{6})_(\d{6})", re.I)
RE_SONY = re.compile(r"^(\d{8})_C\d{4}", re.I)


@dataclass
class SourceFile:
    rel: str                 # chemin relatif au dossier mariage, séparateurs '/'
    kind: str                # 'video' | 'audio'
    device: str              # A7IV, OP4, OP4P, DRONE, CANON, AUDIO, DJI, UNKNOWN...
    group: str               # 1er niveau de dossier (ex. MAIRIE, PREPA HOUPPA RECEPTION)
    size: int
    start: str | None        # ISO local naïf
    start_origin: str        # 'filename' | 'mtime'
    duration: float | None = None
    has_audio: bool | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _device_from_path(parts: tuple[str, ...], name: str) -> str:
    folders = {p.upper().replace(" ", "") for p in parts[:-1]}
    for dev in DEVICE_FOLDERS:
        if dev in folders:
            return dev
    if RE_DJI.search(name):
        return "DJI"
    if RE_CANON.search(name) and "CANON" in name.upper():
        return "CANON"
    if RE_SONY.match(name):
        return "A7IV"
    return "UNKNOWN"


def parse_start(name: str, mtime: float) -> tuple[datetime, str]:
    m = RE_DJI.search(name)
    if m:
        return datetime.strptime(m.group(1), "%Y%m%d%H%M%S"), "filename"
    m = RE_CANON.search(name)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%y%m%d%H%M%S"), "filename"
        except ValueError:
            pass
    m = RE_SONY.match(name)
    if m:
        # date du nom + heure de modification du fichier (fin de prise : approximatif)
        mt = datetime.fromtimestamp(mtime)
        try:
            day = datetime.strptime(m.group(1), "%Y%m%d")
            return day.replace(hour=mt.hour, minute=mt.minute, second=mt.second), "mtime"
        except ValueError:
            pass
    return datetime.fromtimestamp(mtime), "mtime"


def scan(project: Project, subdir: str | None = None) -> list[SourceFile]:
    base = project.root if subdir is None else project.root / subdir
    files: list[SourceFile] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or ".kolnoa" in path.parts:
            continue
        ext = path.suffix.lower()
        if ext in VIDEO_EXT:
            kind = "video"
        elif ext in AUDIO_EXT:
            kind = "audio"
        else:
            continue
        rel_path = path.relative_to(project.root)
        rel = rel_path.as_posix()
        st = path.stat()
        start, origin = parse_start(path.name, st.st_mtime)
        files.append(
            SourceFile(
                rel=rel,
                kind=kind,
                device=_device_from_path(rel_path.parts, path.name),
                group=rel_path.parts[0] if len(rel_path.parts) > 1 else "",
                size=st.st_size,
                start=start.isoformat(timespec="seconds"),
                start_origin=origin,
            )
        )
    return files


def summarize(files: list[SourceFile]) -> dict:
    """Résumé lisible : nombre de fichiers et poids par (groupe, appareil)."""
    out: dict[str, dict] = {}
    for f in files:
        key = f"{f.group or '.'} / {f.device}"
        entry = out.setdefault(key, {"files": 0, "bytes": 0})
        entry["files"] += 1
        entry["bytes"] += f.size
    return out


def save_library(project: Project, files: list[SourceFile]) -> Path:
    project.ensure()
    project.write_json(project.library_path, [f.to_dict() for f in files])
    return project.library_path


def load_library(project: Project) -> list[SourceFile]:
    return [SourceFile(**d) for d in project.read_json(project.library_path)]
