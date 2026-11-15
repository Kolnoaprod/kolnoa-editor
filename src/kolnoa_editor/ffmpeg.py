"""Accès à ffmpeg / ffprobe et choix de l'encodeur (NVENC sur le Shadow PC si dispo)."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def _find(name: str, env_var: str) -> str:
    override = os.environ.get(env_var)
    if override:
        return override
    found = shutil.which(name)
    if not found:
        raise FFmpegError(
            f"{name} introuvable. Installe ffmpeg (ex. `winget install ffmpeg`) "
            f"ou définis la variable {env_var}."
        )
    return found


def ffmpeg_bin() -> str:
    return _find("ffmpeg", "KOLNOA_FFMPEG")


def ffprobe_bin() -> str:
    return _find("ffprobe", "KOLNOA_FFPROBE")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise FFmpegError(f"Commande échouée ({proc.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    return proc


@functools.lru_cache(maxsize=1)
def has_nvenc() -> bool:
    """True si h264_nvenc est réellement utilisable (pas seulement listé)."""
    if os.environ.get("KOLNOA_NO_NVENC"):
        return False
    try:
        run(
            [
                ffmpeg_bin(), "-hide_banner", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
                "-c:v", "h264_nvenc", "-f", "null", "-",
            ]
        )
        return True
    except (FFmpegError, OSError):
        return False


def video_encoder_args(quality: str) -> list[str]:
    """quality: 'proxy' (léger, rapide) ou 'final' (qualité livraison)."""
    if has_nvenc():
        if quality == "proxy":
            return ["-c:v", "h264_nvenc", "-preset", "p2", "-cq", "30"]
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-b:v", "0"]
    if quality == "proxy":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]


def as_posix_arg(path: Path | str) -> str:
    return str(path)
