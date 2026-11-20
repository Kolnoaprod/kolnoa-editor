"""Icône de la zone de notification (près de l'horloge) + menu : Ouvrir · Tâches en cours · Démarrer avec Windows · Quitter.

Vue Qt du moteur résident : masquer/fermer la fenêtre ne touche à aucune tâche ; seul « Quitter » arrête le moteur."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from ..app.engine import ResidentEngine


def kolnoa_icon(size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#1d1f24"))
    p.setPen(QColor("#e8a33d"))
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.setPen(QColor("#e8a33d"))
    f = QFont("Segoe UI", int(size * 0.46), QFont.Bold)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "K")
    p.end()
    return QIcon(pm)


class TrayController(QObject):
    job_event = Signal(str, str)                       # (titre, message) — émis depuis n'importe quel fil, reçu dans le fil Qt

    def __init__(self, app: QApplication, engine: ResidentEngine, window, parent=None):
        super().__init__(parent)
        self.app, self.engine, self.window = app, engine, window
        self.quitting = False
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(kolnoa_icon(), self)
        self.tray.setToolTip("Kolnoa Editor")
        self.menu = QMenu()
        self.act_open = self.menu.addAction("Ouvrir l'éditeur")
        self.act_open.triggered.connect(self.show_window)
        self.jobs_menu = self.menu.addMenu("Tâches en cours")
        self.jobs_menu.aboutToShow.connect(self.refresh_jobs_menu)
        self.menu.addSeparator()
        self.act_auto = self.menu.addAction("Démarrer avec Windows")
        self.act_auto.setCheckable(True)
        self.act_auto.setChecked(engine.settings.start_with_windows)
        self.act_auto.toggled.connect(lambda on: engine.update_settings(start_with_windows=bool(on)))
        self.act_bg = self.menu.addAction("Continuer en arrière-plan à la fermeture")
        self.act_bg.setCheckable(True)
        self.act_bg.setChecked(engine.settings.keep_running_on_close)
        self.act_bg.toggled.connect(lambda on: engine.update_settings(keep_running_on_close=bool(on)))
        self.menu.addSeparator()
        self.act_quit = self.menu.addAction("Quitter Kolnoa Editor")
        self.act_quit.triggered.connect(self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda r: self.show_window() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.job_event.connect(self._notify)
        engine.jobs.subscribe(self._on_job)
        if self.available:
            self.tray.show()

    # --- tâches ------------------------------------------------------------------------------------
    def _on_job(self, job) -> None:
        if job.state == "done":
            self.job_event.emit("Kolnoa Editor", f"Terminé : {job.label}")
        elif job.state == "failed":
            self.job_event.emit("Kolnoa Editor", f"Échec : {job.label} — {job.message}")

    def _notify(self, title: str, message: str) -> None:
        if self.available and not self.window.isVisible():
            self.tray.showMessage(title, message, QSystemTrayIcon.Information, 6000)

    def refresh_jobs_menu(self) -> None:
        self.jobs_menu.clear()
        active = self.engine.jobs.active()
        if not active:
            self.jobs_menu.addAction("Aucune tâche en cours").setEnabled(False)
            return
        for j in active:
            sub = self.jobs_menu.addMenu(f"{j.label} — {int(j.progress * 100)} %")
            a = sub.addAction("Annuler")
            a.triggered.connect(lambda _=False, jid=j.id: self.engine.jobs.cancel(jid))

    # --- fenêtre / sortie --------------------------------------------------------------------------
    def show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def hide_to_tray(self) -> bool:
        """Appelé par la fenêtre à la fermeture : True = masquée (le moteur continue), False = fermeture réelle."""
        if self.quitting or not (self.available and self.engine.settings.keep_running_on_close):
            return False
        self.window.hide()
        if not self.engine.settings.close_notice_shown:
            self.engine.update_settings(close_notice_shown=True)
            self.tray.showMessage("Kolnoa Editor continue en arrière-plan",
                                  "Clic droit sur l'icône pour ouvrir l'éditeur, voir les tâches ou quitter complètement.",
                                  QSystemTrayIcon.Information, 8000)
        return True

    def quit(self, confirm: bool = True) -> bool:
        active = self.engine.jobs.active()
        if active and confirm:
            r = QMessageBox.question(None, "Quitter Kolnoa Editor",
                                     f"{len(active)} tâche(s) en cours seront annulées. Quitter quand même ?")
            if r != QMessageBox.Yes:
                return False
        self.quitting = True
        self.engine.shutdown()
        self.tray.hide()
        self.window.close()
        self.app.quit()
        return True
