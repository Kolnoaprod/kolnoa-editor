"""Lecteur d'angle Qt (QMediaPlayer + QVideoSink). Seul module du lecteur qui importe PySide6.

Décodage matériel : avec le backend FFmpeg de Qt 6, `QT_FFMPEG_DECODING_HW_DEVICE_TYPES` (ex. « cuda,d3d11va ») choisit les
API matérielles autorisées ; `configure_hw()` à appeler AVANT de créer QApplication. Que le GPU décode réellement doit être
vérifié sur la machine (colonne `dec` de `nvidia-smi dmon`, voir tools/bench/hwcheck.py) : le bac à sable n'a pas de GPU.

Priorité : Qt n'expose pas de priorité de décodeur. Elle est donc obtenue par le planificateur (qualité, rôles, budget) et par
l'ordre de recalage ; `set_priority` mémorise le niveau et sert aux mesures.
"""

from __future__ import annotations

import os
import time

HW_DEFAULT = "cuda,d3d11va"


def configure_hw(types: str | None = HW_DEFAULT, backend: str | None = "ffmpeg") -> None:
    if backend:
        os.environ.setdefault("QT_MEDIA_BACKEND", backend)
    if types:
        os.environ.setdefault("QT_FFMPEG_DECODING_HW_DEVICE_TYPES", types)


class QtAngleBackend:
    def __init__(self, on_frame=None):
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        self.player = QMediaPlayer()
        self.sink = QVideoSink()
        self.player.setVideoSink(self.sink)
        self.player.setAudioOutput(None)           # l'audio vient du maître, pas des angles
        self.sink.videoFrameChanged.connect(self._frame)
        self._frame_t = -1.0
        self._frame_wall = 0.0
        self.frames = 0
        self.priority = 9
        self.on_frame = on_frame
        self.path = None

    def _frame(self, frame) -> None:
        st = frame.startTime()
        if st >= 0:
            self._frame_t = st / 1e6
        self._frame_wall = time.perf_counter()
        self.frames += 1
        if self.on_frame:
            self.on_frame(self, frame)

    def open(self, path: str, quality: str) -> None:
        from PySide6.QtCore import QUrl
        self.path = path
        self._frame_t = -1.0
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.pause()

    def close(self) -> None:
        self.player.stop()
        self.player.setSource(__import__("PySide6.QtCore", fromlist=["QUrl"]).QUrl())
        self.path = None

    def seek(self, t: float) -> None:
        self.player.setPosition(int(t * 1000))

    def play(self, rate: float) -> None:
        self.player.setPlaybackRate(rate)
        self.player.play()

    def pause(self) -> None:
        self.player.pause()

    def position(self) -> float:
        return self.player.position() / 1000.0

    def has_frame_near(self, t: float, tol: float) -> bool:
        return self.path is not None and self._frame_t >= 0 and abs(self._frame_t - t) <= tol

    def set_priority(self, level: int) -> None:
        self.priority = level
