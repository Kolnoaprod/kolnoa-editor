"""Création des proxys légers (540p) : base de l'analyse, de la revue et des rendus rapides.

Les rushes 4K (certains pèsent 15-25 Go) ne sont lus qu'au rendu final.

Fiabilité : chaque proxy a une fiche (<proxy>.json) qui mémorise l'empreinte du fichier source
(taille + date) et la hauteur demandée. Un proxy est refait si la source a changé (même avec une
date plus ancienne), si la hauteur change, ou si la fiche manque. L'encodage se fait dans un
fichier .part renommé à la fin : un arrêt en cours de route ne laisse jamais un proxy tronqué, et
relancer la commande reprend là où elle s'est arrêtée.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .ffmpeg import CancelToken, Cancelled, FFmpegError, encoder_name, ffmpeg_bin, run, video_encoder_args
from .project import Project
from .scanner import SourceFile, refresh

log = logging.getLogger("kolnoa.proxies")

META_SCHEMA = 2


def meta_path(dst: Path) -> Path:
    return dst.with_name(dst.name + ".json")


def _fingerprint(src: Path) -> list[int]:
    st = src.stat()
    return [st.st_size, st.st_mtime_ns]


def proxy_state(src: Path, dst: Path, height: int | None = None) -> str:
    """'ok' | 'missing' | 'stale'. height=None : ne compare pas la hauteur (revue, rendu rapide)."""
    if not dst.is_file() or dst.stat().st_size == 0:
        return "missing"
    mp = meta_path(dst)
    try:
        import json

        meta = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "stale"
    if meta.get("schema") != META_SCHEMA or meta.get("source") != _fingerprint(src):
        return "stale"
    if height is not None and meta.get("height") != height:
        return "stale"
    return "ok"


def make_proxy(src: Path, dst: Path, height: int = 540, cancel: CancelToken | None = None) -> bool:
    """Retourne True si un proxy a été (re)créé, False s'il était déjà à jour."""
    if proxy_state(src, dst, height) == "ok":
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = _fingerprint(src)  # pris avant l'encodage : si la source change pendant, le proxy sera refait
    tmp = dst.with_name(dst.stem + ".part.mp4")
    cmd = [
        ffmpeg_bin(), "-y", "-hide_banner", "-v", "error",
        "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"scale=-2:{height}",
        *video_encoder_args("proxy"),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        run(cmd, cancel)
        meta_path(dst).unlink(missing_ok=True)  # pas de fiche pendant le remplacement
        os.replace(tmp, dst)
        Project.write_json(
            meta_path(dst),
            {"schema": META_SCHEMA, "source": fingerprint, "height": height, "encoder": encoder_name()},
        )
    finally:
        tmp.unlink(missing_ok=True)
    return True


def clean_partials(project: Project) -> int:
    """Supprime les .part laissés par un arrêt brutal (dans le dossier de données uniquement)."""
    count = 0
    if project.proxies_dir.exists():
        for p in project.proxies_dir.rglob("*.part.mp4"):
            p.unlink(missing_ok=True)
            count += 1
    return count


@dataclass
class ProxyReport:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False

    def __iter__(self):  # compatibilité : `created, skipped = build_proxies(...)`
        yield len(self.created)
        yield len(self.skipped)


def build_proxies(
    project: Project,
    files: list[SourceFile] | None = None,
    height: int = 540,
    on_progress: Callable[[int, int, str], None] | None = None,
    cancel: CancelToken | None = None,
) -> ProxyReport:
    """Crée les proxys manquants ou périmés. Un fichier illisible n'arrête pas les autres."""
    project.ensure()
    clean_partials(project)
    if files is None:
        files, _ = refresh(project)  # jamais de bibliothèque périmée
    videos = [f for f in files if f.kind == "video" and not f.missing]
    report = ProxyReport()
    for i, f in enumerate(videos, 1):
        if on_progress:
            on_progress(i, len(videos), f.rel)
        try:
            if make_proxy(project.resolve(f.rel), project.proxy_path(f.rel), height, cancel):
                report.created.append(f.rel)
            else:
                report.skipped.append(f.rel)
        except Cancelled:
            report.cancelled = True
            break
        except (FFmpegError, OSError) as exc:
            log.error("proxy échoué pour %s : %s", f.rel, exc)
            report.failed.append((f.rel, str(exc).splitlines()[0]))
    return report
