"""Fenêtre de l'application Kolnoa Editor : rushes · timeline · panneau de commandes · enregistrer / rouvrir · exporter.

Toute la logique est dans `ProjectController` (sans Qt) ; cette fenêtre ne fait que la montrer et lui transmettre les gestes :
- clic sur un plan = sélection ; I / O = points d'entrée / sortie (zone) ; Échap = tout désélectionner ;
- la ligne de commande agit sur la ZONE I–O si elle est posée, sinon sur les plans sélectionnés ;
- « Monte-moi cette séquence » (bouton ou texte) : génère sur la timeline, annulable d'un geste (Ctrl+Z).
Lecteur multicam intégré : lot suivant (le composant existe : `MulticamView`)."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from ..app import ProjectController
from ..timeline import Selection, TimelineError
from .peak_service import PeakService
from .timeline_view import TimelineView


class AppWindow(QMainWindow):
    def __init__(self, controller: ProjectController, exporter=None, analyzer=None):
        super().__init__()
        self.ctl = controller
        self.exporter = exporter                 # (controller, chemin) -> rapport ; injectable (tests, lot J1-b)
        self.analyzer = analyzer
        self.setWindowTitle("Kolnoa Editor")
        self.resize(1500, 900)
        self.peaks = PeakService(cache_dir=self.ctl.project.data / "peaks")
        self.tlv = TimelineView(self.ctl.session, peak_service=self.peaks,
                                source_len=lambda m: self.ctl.rushes[m].duration if m in self.ctl.rushes else None)
        self.rushes = QListWidget()
        self.rushes.setMaximumWidth(340)
        self.prompt = QLineEdit()
        self.prompt.setPlaceholderText("Dites ce que vous voulez : « Monte-moi cette séquence », « raccourcis cette partie de 5 s », "
                                       "« accélère le rythme », « remplace par un plan de coupe »")
        self.status = QLabel("Prêt.")
        self.zone = QLabel("Zone : aucune  ·  I / O pour poser l'entrée / la sortie")
        self.mark_in = self.mark_out = None
        self._build_ui()
        self.tlv.edited.connect(self._edited)
        self.tlv.rejected.connect(lambda m: self.say("Refusé : " + m))
        self.tlv.selectionChanged.connect(lambda ids: self._update_zone())
        self.refresh()

    # --- construction ----------------------------------------------------------------------------------
    def _build_ui(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&Fichier")
        for text, key, fn in (("Importer les rushes…", None, self.import_rushes), ("Enregistrer", "Ctrl+S", self.save),
                              ("Exporter en MP4…", "Ctrl+E", self.export)):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            a.triggered.connect(lambda _=False, fn=fn: fn())
            f.addAction(a)
        e = mb.addMenu("&Édition")
        for text, key, fn in (("Annuler", "Ctrl+Z", self.undo), ("Rétablir", "Ctrl+Shift+Z", self.redo),
                              ("Point d'entrée", "I", self.set_in), ("Point de sortie", "O", self.set_out),
                              ("Effacer la zone", "Alt+X", self.clear_zone)):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(lambda _=False, fn=fn: fn())
            e.addAction(a)
        bar = QHBoxLayout()
        b = QPushButton("Monte-moi cette séquence")
        b.clicked.connect(lambda: self.run_prompt("Monte-moi cette séquence"))
        send = QPushButton("Envoyer")
        send.clicked.connect(lambda: self.run_prompt(self.prompt.text()))
        self.prompt.returnPressed.connect(lambda: self.run_prompt(self.prompt.text()))
        bar.addWidget(b)
        bar.addWidget(self.prompt, 1)
        bar.addWidget(send)
        left = QVBoxLayout()
        left.addWidget(QLabel("Rushes par séquence (sélectionner une séquence pour ne monter qu'elle)"))
        left.addWidget(self.rushes, 1)
        lw = QWidget()
        lw.setLayout(left)
        top = QSplitter(Qt.Horizontal)
        top.addWidget(lw)
        top.addWidget(self.tlv)
        top.setStretchFactor(1, 6)
        lay = QVBoxLayout()
        lay.addWidget(top, 1)
        lay.addWidget(self.zone)
        lay.addLayout(bar)
        lay.addWidget(self.status)
        w = QWidget()
        w.setLayout(lay)
        self.setCentralWidget(w)

    def say(self, text: str) -> None:
        self.status.setText(text)

    def refresh(self) -> None:
        self.rushes.clear()
        for seq, n in self.ctl.sequences().items():
            it = QListWidgetItem(f"{seq or '(sans dossier)'}   ·   {n} rush(es)")
            it.setData(Qt.UserRole, seq)
            self.rushes.addItem(it)
        self.tlv.refresh_model()
        self._update_zone()

    def _edited(self, label: str) -> None:
        self.say(label)
        self._update_zone()

    # --- zone d'entrée / sortie ---------------------------------------------------------------------------
    def set_in(self) -> None:
        self.mark_in = self.tlv.playhead
        if self.mark_out is not None and self.mark_out <= self.mark_in:
            self.mark_out = None
        self._update_zone()

    def set_out(self) -> None:
        self.mark_out = self.tlv.playhead
        if self.mark_in is not None and self.mark_in >= self.mark_out:
            self.mark_in = None
        self._update_zone()

    def clear_zone(self) -> None:
        self.mark_in = self.mark_out = None
        self.tlv.selection = []
        self._update_zone()

    def _update_zone(self) -> None:
        self.tlv.set_marks(self.mark_in, self.mark_out)
        fps = self.ctl.tl.fps
        if self.mark_in is not None and self.mark_out is not None:
            self.zone.setText(f"Zone : {self.mark_in / fps:.1f} s → {self.mark_out / fps:.1f} s ({(self.mark_out - self.mark_in) / fps:.1f} s)")
        elif self.tlv.selection:
            self.zone.setText(f"Sélection : {len(self.tlv.selection)} plan(s)")
        else:
            self.zone.setText("Zone : aucune  ·  I / O pour poser l'entrée / la sortie")

    def current_selection(self) -> Selection | None:
        tl = self.ctl.tl
        if self.mark_in is not None and self.mark_out is not None:
            return Selection.range(tl, self.mark_in, self.mark_out)
        if self.tlv.selection:
            return Selection.of_clips(tl, self.tlv.selection)
        return None

    # --- actions ------------------------------------------------------------------------------------------
    def import_rushes(self, folder: str | None = None) -> None:
        try:
            n = self.ctl.import_rushes(folder)
        except Exception as e:                              # noqa: BLE001 — message clair à l'utilisateur
            self.say(f"Import impossible : {e}")
            return
        self.refresh()
        self.say(f"{n} rush(es) vidéo importé(s) — rien n'a été modifié dans vos fichiers.")

    def selected_sequence(self) -> str | None:
        it = self.rushes.currentItem()
        return it.data(Qt.UserRole) if it else None

    def run_prompt(self, text: str) -> bool:
        """Exécute une demande ; renvoie True si elle a été appliquée. Toute erreur est affichée, jamais silencieuse."""
        text = text.strip()
        if not text:
            return False
        try:
            from ..auto.directives import parse_directive
            d = parse_directive(text)
            if d.kind == "build":
                rep = self.ctl.build(text, self.selected_sequence(), analyzer=self.analyzer)
                self.say(f"{rep.shots} plans posés ({', '.join(rep.sections)}). Ctrl+Z annule toute la génération.")
            else:
                sel = self.current_selection()
                r = self.ctl.command(text, sel, analyzer=self.analyzer)
                self.say(r.message + " — Ctrl+Z pour annuler.")
        except TimelineError as e:
            self.say(f"Impossible : {e}")
            return False
        self.prompt.clear()
        self.refresh()
        return True

    def undo(self) -> None:
        try:
            self.say(self.ctl.undo())
        except TimelineError as e:
            self.say(str(e))
        self.refresh()

    def redo(self) -> None:
        try:
            self.say("rétabli : " + self.ctl.session.redo())
        except TimelineError as e:
            self.say(str(e))
        self.refresh()

    def save(self) -> None:
        p = self.ctl.save()
        self.say(f"Enregistré : {p.name} (le projet se rouvre exactement dans cet état).")

    def export(self, path: str | None = None) -> None:
        if self.exporter is None:
            self.say("L'export MP4 arrive avec le lot de rendu J1-b (PR séparée).")
            return
        path = path or QFileDialog.getSaveFileName(self, "Exporter en MP4", str(self.ctl.project.renders_dir / "film.mp4"), "MP4 (*.mp4)")[0]
        if not path:
            return
        try:
            rep = self.exporter(self.ctl, Path(path))
            self.say(f"Exporté : {path}")
        except Exception as e:                              # noqa: BLE001
            self.say(f"Export impossible : {e}")
            QMessageBox.warning(self, "Export", str(e))

    def closeEvent(self, e) -> None:
        self.peaks.close()
        super().closeEvent(e)


def run_app(argv: list[str]) -> int:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(argv)
    folder = argv[1] if len(argv) > 1 and Path(argv[1]).is_dir() else QFileDialog.getExistingDirectory(None, "Dossier de rushes du projet")
    if not folder:
        return 0
    ctl = ProjectController.open(folder)
    win = AppWindow(ctl)
    win.show()
    return app.exec()


if __name__ == "__main__":
    import sys
    raise SystemExit(run_app(sys.argv))
