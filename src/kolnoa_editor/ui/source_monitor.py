"""Moniteur Source : visionner un rush (curseur, lecture image par image à cadence réduite), poser I/O, puis le placer sur la timeline.
Lecture seule du rush ; ne parle au moteur que via le signal `placeRequested` (le contrôleur de fenêtre applique `place_rush`)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from .media_panel import fmt_duration
from .preview import grab_frame


class SourceMonitor(QWidget):
    placeRequested = Signal(str, float, float)                  # media, src_in, durée (s)

    def __init__(self, media_root: Path, parent=None):
        super().__init__(parent)
        self.root = Path(media_root)
        self.media: str | None = None
        self.duration = 0.0
        self.mark_in = 0.0
        self.mark_out: float | None = None
        self.image = QLabel("Double-cliquez un rush dans la médiathèque pour le visionner ici.")
        self.image.setWordWrap(True)
        self.image.setObjectName("monitor")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumHeight(200)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.valueChanged.connect(self._moved)
        self.time = QLabel("0:00 / 0:00")
        self.time.setObjectName("timecode")
        self.range_label = QLabel("Passage : rush entier")
        self.range_label.setObjectName("zone")
        self.play = QPushButton("▶")
        self.play.setObjectName("play")
        self.play.clicked.connect(self.toggle)
        b_in = QPushButton("[ Entrée")
        b_in.setObjectName("chip")
        b_in.clicked.connect(self.set_in)
        b_out = QPushButton("Sortie ]")
        b_out.setObjectName("chip")
        b_out.clicked.connect(self.set_out)
        self.place = QPushButton("Placer à la tête de lecture")
        self.place.setObjectName("primary")
        self.place.setEnabled(False)
        self.place.clicked.connect(self._place)
        row = QHBoxLayout()
        row.addWidget(self.play)
        row.addWidget(self.time)
        row.addStretch(1)
        row.addWidget(self.range_label)
        row.addWidget(b_in)
        row.addWidget(b_out)
        row.addWidget(self.place)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.image, 1)
        lay.addWidget(self.slider)
        lay.addLayout(row)
        self._grab = QTimer(self)
        self._grab.setSingleShot(True)
        self._grab.setInterval(50)
        self._grab.timeout.connect(self._show_frame)
        self._tick = QTimer(self)
        self._tick.setInterval(200)                              # aperçu de lecture : 5 images/s, sans son (le son se juge au montage/export)
        self._tick.timeout.connect(self._advance)

    @property
    def position(self) -> float:
        return self.slider.value() / 1000 * self.duration

    def open(self, media: str, duration: float) -> None:
        self.pause()
        self.media, self.duration, self.mark_in, self.mark_out = media, float(duration), 0.0, None
        self.slider.blockSignals(True)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.place.setEnabled(duration > 0)
        self._range()
        self._moved()

    def _range(self) -> None:
        out = self.mark_out if self.mark_out is not None else self.duration
        whole = self.mark_in == 0 and self.mark_out is None
        self.range_label.setText("Passage : rush entier" if whole else f"Passage : {fmt_duration(self.mark_in)} → {fmt_duration(out)} ({out - self.mark_in:.1f} s)")

    def _moved(self, *_):
        self.time.setText(f"{fmt_duration(self.position)} / {fmt_duration(self.duration)}")
        self._grab.start()

    def _show_frame(self) -> None:
        if not self.media:
            return
        png = grab_frame(self.root, self.media, self.position, width=960)
        pm = QPixmap()
        if png and pm.loadFromData(png):
            self.image.setPixmap(pm.scaled(self.image.width(), self.image.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.image.setPixmap(QPixmap())
            self.image.setText("Image indisponible (rush illisible ou introuvable)")

    def set_in(self) -> None:
        self.mark_in = min(self.position, (self.mark_out if self.mark_out is not None else self.duration) - 0.1)
        self.mark_in = max(0.0, self.mark_in)
        self._range()

    def set_out(self) -> None:
        self.mark_out = max(self.position, self.mark_in + 0.1)
        self.mark_out = min(self.mark_out, self.duration)
        self._range()

    def toggle(self) -> None:
        if self._tick.isActive():
            self.pause()
        elif self.media:
            self.play.setText("⏸")
            self._tick.start()

    def pause(self) -> None:
        self._tick.stop()
        self.play.setText("▶")

    def _advance(self) -> None:
        v = self.slider.value() + int(0.2 / max(0.1, self.duration) * 1000)
        if v >= 1000:
            self.slider.setValue(1000)
            self.pause()
        else:
            self.slider.setValue(v)

    def _place(self) -> None:
        if not self.media:
            return
        out = self.mark_out if self.mark_out is not None else self.duration
        self.placeRequested.emit(self.media, self.mark_in, round(out - self.mark_in, 3))
