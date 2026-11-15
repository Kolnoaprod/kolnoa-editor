"""Création des proxys légers (540p) : base de l'analyse, de la revue et des rendus rapides.

Les rushes 4K (certains pèsent 15-25 Go) ne sont lus qu'au rendu final.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .ffmpeg import ffmpeg_bin, run, video_encoder_args
from .project import Project
from .scanner import SourceFile


def make_proxy(src: Path, dst: Path, height: int = 540) -> bool:
    """Retourne True si un proxy a été (re)créé, False s'il était déjà à jour."""
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime and dst.stat().st_size > 0:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
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
        run(cmd)
        tmp.replace(dst)
    finally:
        if tmp.exists():
            tmp.unlink()
    return True


def build_proxies(
    project: Project,
    files: list[SourceFile],
    height: int = 540,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> tuple[int, int]:
    """Crée les proxys des vidéos. Retourne (créés, déjà à jour)."""
    videos = [f for f in files if f.kind == "video"]
    created = skipped = 0
    for i, f in enumerate(videos, 1):
        if on_progress:
            on_progress(i, len(videos), f.rel)
        if make_proxy(project.resolve(f.rel), project.proxy_path(f.rel), height):
            created += 1
        else:
            skipped += 1
    return created, skipped
