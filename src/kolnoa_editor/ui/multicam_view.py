"""Fenêtre du lecteur multicam : angle affiché en grand + vignettes de tous les angles, touches 1-9, espace = lecture.

Aucune logique de montage ici : tout passe par `MulticamPlayer` (horloge, planificateur, prise) ; la prise validée repart
dans le moteur par `Session.do` (une étape d'annulation). Chaque angle a son image courante ; celle de l'angle `shown`
reste affichée jusqu'à ce que l'angle demandé ait une image à l'heure (jamais d'écran noir ni de gel).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget

from ..player.controller import MulticamPlayer
from ..player.qt_backend import QtAngleBackend

TICK_MS = 33


class MulticamView(QWidget):
    committed = Signal(object)        # Transaction à passer à Session.do
    refused = Signal(str)

    def __init__(self, player_factory, parent=None):
        """`player_factory(backend_factory)` → MulticamPlayer (pour que la vue fournisse les backends Qt qui lui envoient les images)."""
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.images: dict[str, QImage] = {}
        self._backends: dict[str, QtAngleBackend] = {}
        self.player: MulticamPlayer = player_factory(self._make_backend)
        self.timer = QTimer(self, interval=TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.paint_ms: list[float] = []
        self._pending = False

    def _make_backend(self, angle: str) -> QtAngleBackend:
        def on_frame(be, frame, a=angle):
            # on ne convertit que ce qui est visible, au plus 1 image en attente de peinture
            if self._pending and a != self.player.shown:
                return
            self.images[a] = frame.toImage()
            self._pending = True
            self.update()
        be = QtAngleBackend(on_frame)
        self._backends[angle] = be
        return be

    def _tick(self) -> None:
        self.player.tick()

    def keyPressEvent(self, e) -> None:
        k = e.key()
        try:
            if Qt.Key_1 <= k <= Qt.Key_9:
                self.player.press(k - Qt.Key_0)
            elif k == Qt.Key_Space:
                if self.player.clock.playing:
                    self.player.pause()
                else:
                    self.player.play(record=bool(e.modifiers() & Qt.ShiftModifier))
            elif k == Qt.Key_Return and self.player.take:
                self.committed.emit(self.player.commit())
            elif k == Qt.Key_Escape:
                self.player.discard()
            else:
                return super().keyPressEvent(e)
        except Exception as exc:     # refus du moteur (angle non synchronisé, touche sans angle…) : on le dit, rien ne casse
            self.refused.emit(str(exc))

    def paintEvent(self, _e) -> None:
        import time
        t0 = time.perf_counter()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d0f13"))
        w, h = self.width(), self.height()
        main = QRectF(0, 0, w, h * 0.78)
        img = self.images.get(self.player.shown)
        if img is not None:
            p.drawImage(self._fit(main, img), img)
        keys = self.player.keys
        tw = w / max(1, len(keys))
        for i, a in enumerate(keys):
            r = QRectF(i * tw + 3, h * 0.80, tw - 6, h * 0.18)
            im = self.images.get(a)
            if im is not None:
                p.drawImage(self._fit(r, im), im)
            p.setPen(QColor("#e8b04a") if a == self.player.shown else QColor("#8a93a3"))
            p.drawRect(r)
            p.drawText(r.adjusted(4, 2, 0, 0), f"{i + 1}  {a}")
        p.end()
        self._pending = False
        self.paint_ms.append((time.perf_counter() - t0) * 1000)

    @staticmethod
    def _fit(r: QRectF, img: QImage) -> QRectF:
        s = min(r.width() / img.width(), r.height() / img.height())
        w, h = img.width() * s, img.height() * s
        return QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h)

    def closeEvent(self, e) -> None:
        self.timer.stop()
        self.player.close()
        super().closeEvent(e)
