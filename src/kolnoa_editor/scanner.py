"""Scan d'un dossier de mariage : classe chaque fichier (caméra, drone, audio) et date chaque prise.

Les dates servent à placer les plans dans le temps, notamment le drone qui n'a pas de son.
Sources de date, par ordre de fiabilité :
  - DJI   : DJI_AAAAMMJJHHMMSS_xxxx_D.MP4 (heure de l'horloge du drone)
  - Canon : A_xxxxCnnnAyymmdd_hhmmss..._CANON.MP4
  - Sony  : AAAAMMJJ_Cnnnn.MP4 (date seulement) -> l'heure vient de la date de modification
  - sinon : date de modification du fichier
Chaque appareil a sa propre horloge : un décalage par appareil est à calibrer (voir sync).

La bibliothèque (library.json) est mise à jour de façon incrémentale par `refresh` : un nouveau
scan ne perd jamais les métadonnées déjà lues (durée, audio, empreinte) des fichiers inchangés,
et un scan partiel (--subdir) ne touche pas au reste.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, asdict, fields
from datetime import datetime
from pathlib import Path

from .project import Project

log = logging.getLogger("kolnoa.scanner")

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
    mtime_ns: int = 0        # empreinte (avec size) : détecte tout fichier remplacé ou modifié
    quick_hash: str | None = None   # empreinte de contenu optionnelle (--hash)
    missing: bool = False    # présent dans la bibliothèque mais plus sur le disque

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SourceFile":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def fingerprint(self) -> tuple[int, int]:
        return (self.size, self.mtime_ns)


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


def compute_quick_hash(path: Path) -> str:
    """Empreinte rapide (taille + 3 Mio échantillonnés) : utilisable même sur des fichiers de 25 Go."""
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    chunk = 1 << 20
    with path.open("rb") as fh:
        for offset in (0, max(0, size // 2 - chunk // 2), max(0, size - chunk)):
            fh.seek(offset)
            h.update(fh.read(chunk))
    return h.hexdigest()[:32]


def scan(project: Project, subdir: str | None = None, *, cancel=None) -> list[SourceFile]:
    """Parcourt le disque (aucune lecture de contenu) et retourne les fichiers média trouvés."""
    base = project.root if subdir is None else project.root / subdir
    files: list[SourceFile] = []
    paths = []
    for path in base.rglob("*"):
        if cancel:
            cancel.raise_if_cancelled()
        paths.append(path)
    for path in sorted(paths):
        if cancel:
            cancel.raise_if_cancelled()
        if not path.is_file() or project.is_in_data_dir(path) or ".kolnoa" in path.parts:
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
                mtime_ns=st.st_mtime_ns,
            )
        )
    return files


# --- bibliothèque incrémentale ----------------------------------------
@dataclass
class RefreshReport:
    added: list[str]
    changed: list[str]
    recovered: list[str]
    missing: list[str]
    removed: list[str]
    unchanged: int

    def summary(self) -> str:
        parts = [
            f"{len(self.added)} nouveaux", f"{len(self.changed)} modifiés",
            f"{self.unchanged} inchangés", f"{len(self.missing)} manquants",
        ]
        if self.recovered:
            parts.append(f"{len(self.recovered)} retrouvés")
        if self.removed:
            parts.append(f"{len(self.removed)} retirés")
        return ", ".join(parts)


def load_library(project: Project) -> list[SourceFile]:
    return [SourceFile.from_dict(d) for d in project.read_json(project.library_path)]


def _load_tolerant(project: Project) -> dict[str, SourceFile]:
    path = project.library_path
    if not path.is_file():
        return {}
    try:
        return {f.rel: f for f in load_library(project)}
    except (ValueError, TypeError, KeyError) as exc:
        saved = project.quarantine(path)
        log.warning("library.json illisible (%s), mis de côté : %s", exc, saved)
        return {}


def save_library(project: Project, files: list[SourceFile]) -> Path:
    project.ensure()
    project.write_json(project.library_path, [f.to_dict() for f in files])
    return project.library_path


def refresh(
    project: Project,
    subdir: str | None = None,
    probe_media: bool = False,
    with_hash: bool = False,
    prune: bool = False,
    *, cancel=None, on_progress=None,
) -> tuple[list[SourceFile], RefreshReport]:
    """Met à jour la bibliothèque sans rien perdre.

    - fichier inchangé (taille + date) : toute sa fiche est conservée ;
    - fichier modifié ou remplacé : fiche recréée (durée, empreinte à relire) ;
    - fichier disparu : marqué `missing` (retiré seulement avec prune=True) ;
    - scan partiel (subdir) : les fichiers hors périmètre ne sont pas touchés.
    """
    if on_progress:
        on_progress("Inventaire des fichiers", None, None, None, "Inventaire du dossier de rushes")
    scanned = {f.rel: f for f in scan(project, subdir, cancel=cancel)}
    library = _load_tolerant(project)
    prefix = None if subdir is None else subdir.strip("/\\").replace("\\", "/") + "/"

    def in_scope(rel: str) -> bool:
        return prefix is None or rel.startswith(prefix)

    added, changed, recovered, missing, removed = [], [], [], [], []
    unchanged = 0
    for rel, new in scanned.items():
        old = library.get(rel)
        if old is None:
            library[rel] = new
            added.append(rel)
            continue
        same = old.fingerprint == new.fingerprint or (
            old.mtime_ns == 0 and old.size == new.size  # ancienne bibliothèque sans empreinte
        )
        if same:
            old.mtime_ns = new.mtime_ns
            if old.missing:
                old.missing = False
                recovered.append(rel)
            else:
                unchanged += 1
        else:
            library[rel] = new
            changed.append(rel)
    for rel in [r for r in library if in_scope(r) and r not in scanned]:
        if prune:
            del library[rel]
            removed.append(rel)
        else:
            library[rel].missing = True
            missing.append(rel)

    if probe_media or with_hash:
        from .probe import probe_cached  # import tardif : probe n'est pas nécessaire au simple scan

        targets = [f for f in library.values() if not f.missing and in_scope(f.rel)]
        for completed, f in enumerate(targets):
            if cancel:
                cancel.raise_if_cancelled()
            rel = f.rel
            if on_progress:
                on_progress("Lecture des métadonnées", rel, completed, len(targets) + 1, rel)
            path = project.resolve(rel)
            if with_hash and f.quick_hash is None:
                f.quick_hash = compute_quick_hash(path)
            if probe_media and f.duration is None:
                info = probe_cached(path, cancel=cancel)
                f.duration, f.has_audio = info.duration, info.has_audio

    files = sorted(library.values(), key=lambda f: f.rel)
    if cancel:
        cancel.raise_if_cancelled()
    save_library(project, files)
    report = RefreshReport(added, changed, recovered, missing, removed, unchanged)
    log.info("refresh: %s", report.summary())
    return files, report


def summarize(files: list[SourceFile]) -> dict:
    """Résumé lisible : nombre de fichiers et poids par (groupe, appareil)."""
    out: dict[str, dict] = {}
    for f in files:
        if f.missing:
            continue
        key = f"{f.group or '.'} / {f.device}"
        entry = out.setdefault(key, {"files": 0, "bytes": 0})
        entry["files"] += 1
        entry["bytes"] += f.size
    return out
