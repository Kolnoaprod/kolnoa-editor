"""Exécution d'une opération longue et suivi dans le fil Qt."""

import logging
import time

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

from ..ffmpeg import CancelToken, Cancelled


class OperationWorker(QThread):
    progress = Signal(str, object, object, object, object)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self.work = work
        self.cancel = CancelToken()
        self.result = self.error = None

    def run(self):
        try:
            self.result = self.work(self.cancel, self.progress.emit)
        except Cancelled as error:
            self.error = error
        except Exception as error:
            logging.getLogger(__name__).exception("Opération échouée")
            self.error = error


class OperationDialog(QDialog):
    def __init__(self, title, worker, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowMinimizeButtonHint)
        self.resize(620, 365)
        self.worker = worker
        self.started = time.monotonic()
        self.running = True
        self.stage = QLabel("Préparation…")
        self.stage.setTextFormat(Qt.PlainText)
        self.file = QLabel()
        self.file.setTextFormat(Qt.PlainText)
        self.file.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.elapsed = QLabel("Temps écoulé · 0 s")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setAccessibleName("Journal technique")
        self.log.setMaximumBlockCount(2000)
        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.clicked.connect(self.cancel_or_close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.elapsed)
        bottom.addStretch()
        self.minimize_btn = QPushButton("Réduire")
        self.minimize_btn.clicked.connect(self.showMinimized)
        bottom.addWidget(self.minimize_btn)
        bottom.addWidget(self.cancel_btn)
        layout = QVBoxLayout(self)
        for widget in (self.stage, self.file, self.bar, self.log):
            layout.addWidget(widget)
        layout.addLayout(bottom)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self.update_elapsed)
        self.timer.start()
        worker.progress.connect(self.update_progress)

    def update_elapsed(self):
        minutes, seconds = divmod(int(time.monotonic() - self.started), 60)
        self.elapsed.setText(f"Temps écoulé · {minutes:02d}:{seconds:02d}")

    def update_progress(self, stage, media, done, total, message):
        if not self.worker.cancel.is_set():
            self.stage.setText(stage)
        if media is not None:
            self.file.setText(media)
        if done is not None and total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.bar.setFormat(f"%p % · {done}/{total} unités")
        elif stage != "Enregistrement du montage":
            self.bar.setRange(0, 0)
        if stage == "Enregistrement du montage":
            self.file.clear()
            self.cancel_btn.setEnabled(False)
        if message:
            self.log.appendPlainText(message)

    def finish(self, message, success):
        self.running = False
        self.timer.stop()
        self.update_elapsed()
        self.stage.setText(message)
        self.log.appendPlainText(message)
        if success:
            self.file.clear()
            self.bar.setRange(0, 100)
            self.bar.setValue(100)
            self.bar.setFormat("%p %")
        elif self.bar.maximum() == 0:
            self.bar.setRange(0, 100)
            self.bar.reset()
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Fermer")

    def cancel_or_close(self):
        if self.running:
            self.worker.cancel.cancel()
            self.stage.setText("Annulation en cours…")
            self.cancel_btn.setEnabled(False)
        else:
            self.accept()

    def reject(self):
        if self.running:
            self.cancel_or_close()
        else:
            super().reject()
