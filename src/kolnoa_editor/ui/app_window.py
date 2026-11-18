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
    def __init__(self, controller: ProjectController, exporter=None, analyzer=None, player_factory=None, sync_source_factory=None):
        super().__init__()
        self.player_factory = player_factory       # (controller, group_id) -> widget lecteur (avec `.player`) | None ; défaut : lecteur multicam réel
        self.sync_source_factory = sync_source_factory
        self.player_view = None
        self._player_gid = None
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
        self.view_box = QVBoxLayout()
        vb = QWidget()
        vb.setLayout(self.view_box)
        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.addWidget(vb)
        self.vsplit.addWidget(self.tlv)
        self.vsplit.setStretchFactor(1, 2)
        top = QSplitter(Qt.Horizontal)
        top.addWidget(lw)
        top.addWidget(self.vsplit)
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
        self._ensure_player()
        self._update_zone()

    # --- lecteur multicam ---------------------------------------------------------------------------------
    def _ensure_player(self) -> None:
        tl = self.ctl.tl
        gid = tl.groups[0].id if tl.groups else None
        if gid == self._player_gid:
            return
        if self.player_view is not None:
            self.view_box.removeWidget(self.player_view)
            getattr(self.player_view, "close", lambda: None)()
            self.player_view.deleteLater()
            self.player_view = None
        self._player_gid = gid
        if gid is None:
            return
        try:
            factory = self.player_factory or default_player_factory
            self.player_view = factory(self.ctl, gid)
        except Exception as e:                              # noqa: BLE001 — pas de lecteur (pilote vidéo, proxy absent) : la timeline reste utilisable
            self.say(f"Lecteur indisponible : {e}")
            self.player_view = None
        if self.player_view is not None:
            self.view_box.addWidget(self.player_view)
            self.tlv.playheadChanged.connect(self._seek_player)

    def _seek_player(self, frame: int) -> None:
        pv = self.player_view
        if pv is not None and hasattr(pv, "player"):
            pv.player.seek(frame / self.ctl.tl.fps)

    def angles_of_selection(self) -> list[str]:
        tl = self.ctl.tl
        out: list[str] = []
        for cid in self.tlv.selection:
            c = tl.clip(cid)
            if c.group:
                out = [a.id for a in tl.group(c.group).angles if a.usable and a.kind == "video"]
        return out

    def switch_angle(self, angle_id: str) -> bool:
        """Change l'angle des plans multicam sélectionnés (même instant, même son maître). Un seul pas d'annulation ; protégé comme toute retouche manuelle."""
        from ..timeline import Transaction
        from ..timeline.ops import switch_angle
        tl = self.ctl.tl
        ids = [c for c in self.tlv.selection if tl.clip(c).group]
        if not ids:
            self.say("Sélectionnez d'abord un plan multicam.")
            return False
        try:
            self.ctl.session.do(Transaction([switch_angle(tl, i, angle_id) for i in ids], f"angle {angle_id}"), f"angle {angle_id}")
        except TimelineError as e:
            self.say(f"Refusé : {e}")
            return False
        self.say(f"Angle {angle_id} sur {len(ids)} plan(s).")
        self.refresh()
        return True

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
                rep = self.ctl.build_with_multicam(text, self.selected_sequence(), analyzer=self.analyzer, source_factory=self.sync_source_factory)
                g = rep["groups"]
                skipped = [f"{x['name']}: {', '.join(x['skipped'])}" for x in g if x["skipped"]]
                self.say(f"Montage posé : {len(g)} groupe(s) multicam, {rep['plain']} plan(s) à plat. Ctrl+Z annule toute la génération."
                         + (f" ⚠ angle(s) non synchronisé(s), écarté(s) : {'; '.join(skipped)}" if skipped else ""))
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

    def export(self, path: str | None = None, resolution: str | None = None) -> None:
        if resolution is None:
            from PySide6.QtWidgets import QInputDialog
            labels = {"4K (3840×2160)": "4k", "1080p (1920×1080)": "1080p"}
            native = tuple(self.ctl.tl.resolution)
            names = list(labels)
            cur = 0 if native[0] >= 3840 else 1
            pick, ok = QInputDialog.getItem(self, "Format d'export", "Résolution du MP4 :", names, cur, False)
            if not ok:
                return
            resolution = labels[pick]
        path = path or QFileDialog.getSaveFileName(self, "Exporter en MP4", str(self.ctl.project.renders_dir / "film.mp4"), "MP4 (*.mp4)")[0]
        if not path:
            return
        try:
            rep = (self.exporter or (lambda c, p, r: c.export(p, resolution=r)))(self.ctl, Path(path), resolution)
            warn = getattr(rep, "warnings", None) or []
            self.say(f"Exporté : {path}" + (f" — {len(warn)} avertissement(s) : {warn[0]}" if warn else ""))
        except Exception as e:                              # noqa: BLE001
            self.say(f"Export impossible : {e}")
            QMessageBox.warning(self, "Export", str(e))

    def closeEvent(self, e) -> None:
        self.peaks.close()
        super().closeEvent(e)


def default_player_factory(ctl: ProjectController, gid: str):
    """Lecteur multicam réel : un lecteur par angle (proxy s'il existe, sinon original), planifié par `MulticamPlayer`."""
    from ..player.controller import MulticamPlayer
    from ..player.qt_backend import configure_hw
    from ..player.scheduler import MediaInfo
    from ..probe import probe
    from .multicam_view import MulticamView
    configure_hw()
    media = {}
    for a in ctl.tl.group(gid).angles:
        src = ctl.project.root / a.media
        i = probe(src)
        proxy = ctl.project.proxy_path(a.media)
        media[a.id] = MediaInfo(str(src), i.width or 1920, i.height or 1080, i.fps or 25.0, str(proxy) if proxy.is_file() else None, 540, i.duration)
    return MulticamView(lambda mk: MulticamPlayer(ctl.tl, gid, "V1", media, mk, budget=6e8, hw_decode=True))


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
