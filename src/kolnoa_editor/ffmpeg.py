"""Accès à ffmpeg / ffprobe, exécution annulable et choix de l'encodeur (NVENC sur le Shadow PC)."""

from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

log = logging.getLogger("kolnoa.ffmpeg")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class FFmpegError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    """Opération annulée par l'utilisateur (Ctrl+C ou bouton Annuler)."""


class CancelToken:
    """Jeton d'annulation partagé entre l'interface (ou la CLI) et les traitements."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled("Opération annulée")


def _find(name: str, env_var: str) -> str:
    override = os.environ.get(env_var)
    if override:
        return override
    found = shutil.which(name)
    if not found:
        raise FFmpegError(
            f"{name} introuvable. Installe ffmpeg (ex. `winget install Gyan.FFmpeg`) "
            f"ou définis la variable {env_var}."
        )
    return found


def ffmpeg_bin() -> str:
    return _find("ffmpeg", "KOLNOA_FFMPEG")


def ffprobe_bin() -> str:
    return _find("ffprobe", "KOLNOA_FFPROBE")


def run_hidden(cmd, **kwargs) -> subprocess.CompletedProcess:
    """API de subprocess.run avec un processus sans console sur Windows."""
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | NO_WINDOW
    return subprocess.run(cmd, **kwargs)


def run(cmd: list[str], cancel: CancelToken | None = None, quiet: bool = False, *,
        binary: bool = False, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Exécute une commande. Annulable : le processus est arrêté si `cancel` est déclenché.

    stdout/stderr passent par des fichiers temporaires (pas de blocage de tube sur les longues
    sorties) ; sur Windows aucune fenêtre console n'est ouverte.
    """
    if cancel is not None:
        cancel.raise_if_cancelled()
    log.debug("exec: %s", cmd)
    deadline = time.monotonic() + timeout if timeout is not None else None
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=NO_WINDOW
        )
        try:
            while True:
                try:
                    rc = proc.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if cancel is not None and cancel.is_set():
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        raise Cancelled("Opération annulée")
                    if deadline is not None and time.monotonic() >= deadline:
                        raise FFmpegError(f"Délai dépassé ({timeout:g} s) : {cmd[0]}")
        except BaseException:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            raise
        out.seek(0)
        err.seek(0)
        stdout = out.read()
        stderr = err.read().decode("utf-8", "replace")
    if cancel is not None:
        cancel.raise_if_cancelled()
    if rc != 0 and check:
        tail = "\n".join(stderr.strip().splitlines()[-12:])
        (log.debug if quiet else log.error)("commande échouée (%s): %s\n%s", rc, cmd, stderr[-4000:])
        raise FFmpegError(f"Commande échouée ({rc}): {' '.join(cmd[:6])} ...\n{tail}")
    return subprocess.CompletedProcess(cmd, rc, stdout if binary else stdout.decode("utf-8", "replace"), stderr)


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
            ],
            quiet=True,
        )
        return True
    except (FFmpegError, OSError):
        return False


def encoder_name() -> str:
    return "h264_nvenc" if has_nvenc() else "libx264"


def video_encoder_args(quality: str) -> list[str]:
    """quality: 'proxy' (léger, rapide) ou 'final' (qualité livraison)."""
    if has_nvenc():
        if quality == "proxy":
            return ["-c:v", "h264_nvenc", "-preset", "p2", "-cq", "30"]
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-b:v", "0"]
    if quality == "proxy":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
