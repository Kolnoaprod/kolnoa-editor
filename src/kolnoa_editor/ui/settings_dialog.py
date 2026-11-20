"""Paramètres de l'application résidente : démarrage avec Windows, démarrage réduit, fermeture en arrière-plan."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout


class SettingsDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Paramètres")
        s = engine.settings
        lay = QVBoxLayout(self)
        self.c_auto = QCheckBox("Démarrer Kolnoa Editor avec Windows (à l'ouverture de session)")
        self.c_auto.setChecked(s.start_with_windows)
        self.c_min = QCheckBox("… et démarrer réduit près de l'horloge, sans ouvrir la fenêtre")
        self.c_min.setChecked(s.start_minimized)
        self.c_bg = QCheckBox("Fermer la fenêtre garde le moteur actif (tâches et analyses continuent)")
        self.c_bg.setChecked(s.keep_running_on_close)
        for w in (self.c_auto, self.c_min, self.c_bg):
            lay.addWidget(w)
        lay.addWidget(QLabel("« Quitter » dans le menu de l'icône arrête complètement l'application.\nLe PC en veille ou éteint ne travaille pas."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.apply)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def apply(self) -> None:
        self.engine.update_settings(start_with_windows=self.c_auto.isChecked(), start_minimized=self.c_min.isChecked(),
                                    keep_running_on_close=self.c_bg.isChecked())
        self.accept()
