"""Fenêtre de l'application Kolnoa Editor : rushes · timeline · panneau de commandes · enregistrer / rouvrir · exporter.

Toute la logique est dans `ProjectController` (sans Qt) ; cette fenêtre ne fait que la montrer et lui transmettre les gestes :
- clic sur un plan = sélection ; I / O = points d'entrée / sortie (zone) ; Échap = tout désélectionner ;
- la ligne de commande agit sur la ZONE I–O si elle est posée, sinon sur les plans sélectionnés ;
- « Monte-moi cette séquence » (bouton ou texte) : génère sur la timeline, annulable d'un geste (Ctrl+Z).
Lecteur multicam intégré : lot suivant (le composant existe : `MulticamView`)."""

from __future__ import annotations

import tempfile
from copy import copy
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QKeySequence, QPixmap
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QPushButton, QSplitter, QVBoxLayout, QWidget)

from ..app import ProjectController
from ..app.media_manifest import MediaManifest
from ..auto.music import place_music
from ..auto.rhythm import analyze_music, beat_markers
from ..auto.condensed import BalancedThresholds, MediaScope
from ..auto.playhead import selection_from_playhead
from ..ffmpeg import Cancelled
from ..timeline import Selection, TimelineError
from .operation import OperationDialog, OperationWorker
from .peak_service import PeakService
from .preview import frame_request, grab_frame
from .timeline_view import TimelineView


class AppWindow(QMainWindow):
    def __init__(self, controller: ProjectController, exporter=None, analyzer=None, player_factory=None, sync_source_factory=None, manifest=None):
        super().__init__()
        self.player_factory = player_factory       # (controller, group_id) -> widget lecteur (avec `.player`) | None ; défaut : lecteur multicam réel
        self.sync_source_factory = sync_source_factory
        self.player_view = None
        self._player_gid = None
        self.ctl = controller
        self.media_manifest = manifest or MediaManifest(controller.project.data / "Medias.klnprj")
        self.media_manifest.add_video_references(self.ctl)
        if self.media_manifest.active_sequence is not None:
            self.ctl.session = self.media_manifest.open_sequence(self.ctl, self.media_manifest.active_sequence)
        self.setAcceptDrops(True)
        self.exporter = exporter                 # (controller, chemin) -> rapport ; injectable (tests, lot J1-b)
        self.analyzer = analyzer
        self._operation = None
        self._preview_worker = None
        self._preview_request = None
        self._task_button = QPushButton("Suivi de la tâche")
        self._task_button.clicked.connect(self._show_operation)
        self.statusBar().addPermanentWidget(self._task_button)
        self._task_button.hide()
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
        self.preview = QLabel("Aperçu : placez la tête de lecture sur un plan")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(240)
        self.preview.setStyleSheet("background:#111;color:#888")
        self.timecode = QLabel("00:00:00:00")
        self.play_btn = QPushButton("▶ Lecture")
        self.play_btn.clicked.connect(self.toggle_play)
        self._playing = False
        self._playback_dir = None
        self._playback_key = None
        self._program_player = None
        self._player_clock_update = False
        self._pending_program_seek = None
        self._grab_timer = QTimer(self)
        self._grab_timer.setSingleShot(True)
        self._grab_timer.setInterval(60)
        self._grab_timer.timeout.connect(self._request_preview)
        self.zone = QLabel("Zone : aucune  ·  I / O pour poser l'entrée / la sortie")
        self.mark_in = self.mark_out = None
        self._build_ui()
        self.tlv.edited.connect(self._edited)
        self.tlv.playheadChanged.connect(self._on_playhead)
        self.tlv.rejected.connect(lambda m: self.say("Refusé : " + m))
        self.tlv.selectionChanged.connect(lambda ids: self._update_zone())
        self.tlv.selectionChanged.connect(lambda ids: self.say(self.explain_selection() or self.status.text()))
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
        for text, action in (
            ("Importer des fichiers…", self.choose_external_files),
            ("Importer un dossier…", self.choose_external_folder),
            ("Importer de la musique…", self.choose_music),
            ("Chutier importé…", self.show_imported_media),
            ("Enregistrer le projet .klnprj…", self.save_native_project),
            ("Nouvelle séquence…", self.create_editorial_sequence),
            ("Changer de séquence…", self.choose_editorial_sequence),
        ):
            f.addAction(text, action)
        e = mb.addMenu("&Édition")
        for text, key, fn in (("Annuler", "Ctrl+Z", self.undo), ("Rétablir", "Ctrl+Shift+Z", self.redo),
                              ("Point d'entrée", "I", self.set_in), ("Point de sortie", "O", self.set_out),
                              ("Effacer la zone", "Alt+X", self.clear_zone)):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(lambda _=False, fn=fn: fn())
            e.addAction(a)
        a = QAction("Lecture / pause", self)
        a.setShortcut(QKeySequence("Space"))
        a.triggered.connect(lambda _=False: self.toggle_play())
        e.addAction(a)
        bar = QHBoxLayout()
        b = QPushButton("Monte-moi cette séquence")
        b.clicked.connect(lambda: self.run_prompt("Monte-moi cette séquence"))
        send = QPushButton("Envoyer")
        send.clicked.connect(lambda: self.run_prompt(self.prompt.text()))
        self.prompt.returnPressed.connect(lambda: self.run_prompt(self.prompt.text()))
        condensed = QPushButton("Film condensé (essai)")
        condensed.clicked.connect(self.configure_condensed)
        bar.addWidget(b)
        bar.addWidget(condensed)
        bar.addWidget(self.prompt, 1)
        bar.addWidget(send)
        left = QVBoxLayout()
        left.addWidget(QLabel("Dossiers sources (le montage classique suit le dossier sélectionné)"))
        left.addWidget(self.rushes, 1)
        lw = QWidget()
        lw.setLayout(left)
        self.view_box = QVBoxLayout()
        self.view_box.addWidget(self.preview, 1)
        tb = QHBoxLayout()
        tb.addWidget(self.play_btn)
        tb.addWidget(self.timecode)
        tb.addStretch(1)
        self.view_box.addLayout(tb)
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
        self.pause()
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

    # --- transport + aperçu image (tout montage, multicam ou non) ----------------------------------------
    def toggle_play(self) -> None:
        if self._playing:
            self.pause()
            return
        tl = self.ctl.tl
        total = max((c.end for c in tl.clips if tl.track(c.track).kind == "video"), default=0)
        if total <= 0:
            self.say("Rien à lire : importez des rushes puis lancez « Monte-moi cette séquence ».")
            return
        if self.tlv.playhead >= total - 1:
            self.tlv.set_playhead(0)
        key = tl.canonical()
        if self._playback_key == key and self._program_player is not None:
            self._start_program_player()
            return
        if self._playback_dir is None:
            self.ctl.project.data.mkdir(parents=True, exist_ok=True)
            self._playback_dir = tempfile.TemporaryDirectory(prefix="lecture-", dir=self.ctl.project.data, ignore_cleanup_errors=True)
        if self._program_player is not None:
            previous = self._program_player.source().toLocalFile()
            self._program_player.setSource(QUrl())
            if previous:
                try:
                    Path(previous).unlink(missing_ok=True)
                except PermissionError:
                    pass  # Windows peut garder la source ouverte jusqu'à la fin du décodage.
        self._playback_key = None
        path = Path(self._playback_dir.name) / f"lecture-{uuid4().hex}.mp4"

        def build(cancel, progress):
            progress("Préparation de la lecture", None, None, None, "Rendu vidéo léger et mixage de la timeline")

            def segment(i, count, media):
                assembling = i == count and media == "assemblage"
                progress("Mixage et assemblage" if assembling else "Préparation vidéo", media,
                         count if assembling else i - 1, count + 1, None)

            self.ctl.export(path, kind="proxy", resolution=(1920, 1080), cancel=cancel, on_progress=segment)
            return path

        def ready(path):
            self._open_program_player(path)
            self._playback_key = key
            QTimer.singleShot(0, self._start_program_player)
            QTimer.singleShot(0, self._operation.close)
            return "Aperçu de lecture prêt · son de la timeline inclus."

        self._start_operation("Préparation de la lecture", build, ready)

    def _open_program_player(self, path: Path):
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        if self._program_player is None:
            self._program_player = QMediaPlayer(self)
            self._program_audio = QAudioOutput(self)
            self._program_video = QVideoWidget(self)
            self._program_video.hide()
            self.view_box.insertWidget(0, self._program_video, 1)
            self._program_player.setAudioOutput(self._program_audio)
            self._program_player.setVideoOutput(self._program_video)
            self._program_player.positionChanged.connect(self._update_from_player)
            self._program_player.seekableChanged.connect(self._program_seekable)
            self._program_player.mediaStatusChanged.connect(self._program_status_changed)
            self._program_player.errorOccurred.connect(self._program_error)
        self._program_player.setSource(QUrl.fromLocalFile(str(path)))

    def _start_program_player(self):
        self._grab_timer.stop()
        self._preview_request = None
        self._playing = True
        self.preview.hide()
        self._program_video.show()
        self.play_btn.setText("⏸ Pause")
        self._seek_program(self.tlv.playhead)
        self._program_player.play()

    def _seek_program(self, frame):
        milliseconds = round(frame * 1000 / self.ctl.tl.fps)
        if self._program_player.isSeekable():
            self._pending_program_seek = None
            self._program_player.setPosition(milliseconds)
        else:
            self._pending_program_seek = milliseconds

    def _program_seekable(self, seekable):
        if seekable and self._pending_program_seek is not None:
            position = self._pending_program_seek
            self._pending_program_seek = None
            self._program_player.setPosition(position)

    def _program_status_changed(self, status):
        from PySide6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.pause()

    def _program_error(self, error, message):
        self._playback_key = None
        self.pause()
        self.say(f"Lecture impossible : {message}")

    def _update_from_player(self, milliseconds):
        if not self._playing or self._pending_program_seek is not None:
            return
        self._player_clock_update = True
        try:
            self.tlv.set_playhead(round(milliseconds * self.ctl.tl.fps / 1000))
        finally:
            self._player_clock_update = False

    def pause(self) -> None:
        if self._playing:
            self._playing = False
            self._program_player.pause()
            self._program_video.hide()
            self.preview.show()
            self._grab_timer.start()
        self.play_btn.setText("▶ Lecture")

    def _on_playhead(self, frame: int) -> None:
        fps = self.ctl.tl.fps
        s = frame // fps
        self.timecode.setText(f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{frame % fps:02d}")
        if self._playing:
            if not self._player_clock_update:
                self._seek_program(frame)
        elif not self._grab_timer.isActive():
            self._grab_timer.start()

    def _request_preview(self) -> None:
        if self._playing or (self._operation is not None and self._operation.running):
            return
        request = frame_request(self.ctl.tl, self.tlv.playhead)
        self._preview_request = request
        if request is None:
            self._show_preview(None, black=True)
            return
        if self._preview_worker is not None:
            return
        worker = OperationWorker(lambda cancel, _: grab_frame(self.ctl.project.root, *request, cancel=cancel), self)
        self._preview_worker = worker
        worker.finished.connect(self._preview_finished)
        worker.request = request
        worker.start()

    def _preview_finished(self):
        worker = self._preview_worker
        self._preview_worker = None
        worker.deleteLater()
        if self._operation is not None and self._operation.running:
            return
        if self._preview_request is not None and (worker.request == self._preview_request or (self._playing and worker.result)):
            self._show_preview(worker.result)
        if worker.request != self._preview_request and self._preview_request is not None:
            self._request_preview()

    def _update_preview(self) -> None:
        req = frame_request(self.ctl.tl, self.tlv.playhead)
        png = grab_frame(self.ctl.project.root, *req) if req else None
        self._show_preview(png, black=req is None)

    def _show_preview(self, png, *, black=False):
        if black:
            self.preview.setText("Noir (aucun plan à cette image)")
            return
        pm = QPixmap()
        if png and pm.loadFromData(png):
            self.preview.setPixmap(pm.scaled(self.preview.width(), self.preview.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.preview.setText("Image indisponible (rush illisible ou introuvable)")

    def _seek_player(self, frame: int) -> None:
        pv = self.player_view
        if not self._playing and pv is not None and hasattr(pv, "player"):
            pv.player.seek(frame / self.ctl.tl.fps)

    def angles_of_selection(self) -> list[str]:
        tl = self.ctl.tl
        out: list[str] = []
        for cid in self.tlv.selection:
            c = tl.clip(cid)
            if c.group:
                out = [a.id for a in tl.group(c.group).angles if a.usable and a.kind == "video"]
        return out

    def explain_selection(self) -> str:
        """Pourquoi cet angle : motif, scores de chaque angle fiable, et alternatives (les non fiables sont dits écartés)."""
        tl = self.ctl.tl
        for cid in self.tlv.selection:
            c = tl.clip(cid)
            if not c.group:
                continue
            g = tl.group(c.group)
            why = (c.extra or {}).get("angle_choice")
            reliable = [a.id for a in g.angles if a.usable and a.kind == "video"]
            out = [a.id for a in g.angles if not a.usable and a.kind == "video"]
            txt = f"Angle {c.angle} — "
            if c.angle and self.ctl.session.state_of(cid) == "manuel":
                txt += "choisi à la main"
            elif why:
                txt += why["motif"]
            else:
                txt += "choix non documenté"
            if why and why.get("scores"):
                txt += " · scores : " + ", ".join(f"{k} {v:.2f}" for k, v in sorted(why["scores"].items()))
            txt += f" · alternatives fiables : {', '.join(a for a in reliable if a != c.angle) or 'aucune'}"
            if out:
                txt += f" · écartés (synchro non fiable) : {', '.join(out)}"
            return txt
        return ""

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
        self.pause()
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
        return selection_from_playhead(tl, self.tlv.playhead)

    # --- actions ------------------------------------------------------------------------------------------
    def _show_operation(self):
        if self._operation is not None:
            self._operation.showNormal()
            self._operation.raise_()
            self._operation.activateWindow()

    def _start_operation(self, title, work, apply):
        if self._operation is not None:
            if self._operation.running:
                self._show_operation()
                return False
            self._operation.close()
            self._operation.deleteLater()
        self.pause()
        self._grab_timer.stop()
        if self._preview_worker is not None:
            self._preview_worker.cancel.cancel()
        worker = OperationWorker(work, self)
        dialog = OperationDialog(title, worker, self)
        self._operation = dialog
        self._task_button.setText("Suivi de la tâche")
        self._task_button.show()
        self.centralWidget().setEnabled(False)
        self.menuBar().setEnabled(False)
        dialog.show()

        def finished():
            try:
                if worker.error is not None:
                    raise worker.error
                message = apply(worker.result)
                success = True
            except Cancelled:
                message, success = "Opération annulée.", False
            except Exception as error:
                message, success = f"Impossible : {error}", False
            self.centralWidget().setEnabled(True)
            self.menuBar().setEnabled(True)
            dialog.finish(message, success)
            self._task_button.setText("Journal de la tâche")
            self.say(message)
            worker.deleteLater()

        worker.finished.connect(finished)
        worker.start()
        return True

    def import_rushes(self, folder: str | None = None) -> None:
        def work(cancel, progress):
            draft = copy(self.ctl)
            draft.rushes = self.ctl.rushes.copy()
            with self.ctl.session.staged(cancel.raise_if_cancelled) as draft.session:
                count = draft.import_rushes(folder, cancel=cancel, on_progress=progress)
            return draft, count

        def apply(result):
            draft, count = result
            self.ctl.session.adopt(draft.session)
            self.ctl.rushes = draft.rushes
            self.refresh()
            return f"{count} rush(es) vidéo importé(s)."

        self._start_operation("Importation des rushes", work, apply)

    def choose_external_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Importer des médias")
        if paths:
            self.import_references(paths)

    def choose_external_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Importer un dossier de médias")
        if folder:
            from PySide6.QtWidgets import QMessageBox
            recursive = QMessageBox.question(self, "Importer un dossier", "Inclure les sous-dossiers ?") == QMessageBox.Yes
            self.import_references([folder], recursive=recursive)

    def choose_music(self):
        from PySide6.QtWidgets import QMessageBox
        choice = QMessageBox.question(self, "Importer de la musique", "Importer un dossier musical ?",
                                      QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if choice == QMessageBox.Yes:
            folder = QFileDialog.getExistingDirectory(self, "Bibliothèque musicale")
            if folder:
                self.import_references([folder], music=True, recursive=True)
        elif choice == QMessageBox.No:
            paths, _ = QFileDialog.getOpenFileNames(self, "Fichiers musicaux")
            if paths:
                self.import_references(paths, music=True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            self.import_references([url.toLocalFile() for url in event.mimeData().urls()])
        else:
            super().dropEvent(event)

    def import_references(self, paths, **options):
        def work(cancel, progress):
            draft = MediaManifest(self.media_manifest.path)
            draft.bind(self.ctl)
            added = draft.import_paths(paths, cancel=cancel, progress=progress, **options)
            return draft, added

        def apply(result):
            self.media_manifest, added = result
            self.media_manifest.add_video_references(self.ctl)
            self.refresh()
            return f"{len(added)} nouvelle(s) référence(s) dans le chutier importé."

        self._start_operation("Importation multimédia", work, apply)

    def save_native_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer le projet", "", "Projets Kolnoa (*.klnprj)")
        if not path:
            return
        manifest = self.media_manifest
        previous = manifest.path
        manifest.path = Path(path).with_suffix(".klnprj")
        try:
            manifest.bind(self.ctl)
            manifest.save()
        except OSError as error:
            manifest.path = previous
            self.say(f"Enregistrement impossible : {error}")
        else:
            self.say(f"Projet enregistré · {manifest.path.name}")

    def create_editorial_sequence(self):
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "Nouvelle séquence", "Nom")
        if ok:
            try:
                entry = self.media_manifest.create_sequence(self.ctl, name)
                self.switch_editorial_sequence(entry.id)
            except (OSError, ValueError, TimelineError) as error:
                self.say(f"Création impossible : {error}")

    def choose_editorial_sequence(self):
        from PySide6.QtWidgets import QInputDialog

        entries = {"Projet · montage historique": None}
        entries.update({f"Séquence · {seq.title}": seq.id for seq in self.media_manifest.sequences})
        name, ok = QInputDialog.getItem(self, "Séquences du projet", "Séquence", list(entries), editable=False)
        if ok:
            try:
                self.switch_editorial_sequence(entries[name])
            except (OSError, ValueError, TimelineError) as error:
                self.say(f"Ouverture impossible : {error}")

    def switch_editorial_sequence(self, sequence_id):
        session = self.media_manifest.open_sequence(self.ctl, sequence_id)
        self.pause()
        self._grab_timer.stop()
        self._preview_request = None
        self._player_gid = object()
        self.ctl.session = self.tlv.session = session
        self.tlv.selection = []
        self.mark_in = self.mark_out = None
        self.tlv.set_playhead(0)
        self._show_preview(None, black=True)
        self.refresh()
        self.say(f"Séquence active · {session.tl.title}")

    def show_imported_media(self):
        from PySide6.QtWidgets import QInputDialog

        categories = {"video": "Vidéo", "music": "Musique", "audio": "Audio", "image": "Photo"}
        items = {f"{categories[ref.kind]} · {ref.title} · {ref.path}": ref for ref in self.media_manifest.media}
        if not items:
            self.say("Le chutier importé est vide.")
            return
        name, ok = QInputDialog.getItem(self, "Chutier importé", "Média", list(items), editable=False)
        if ok and items[name].kind == "music":
            actions = ["Analyser le tempo", "Poser sur la piste musique et créer les repères"]
            action, chosen = QInputDialog.getItem(self, "Musique", "Action", actions, editable=False)
            if chosen:
                self.analyze_music_reference(items[name], place=action == actions[1])

    def analyze_music_reference(self, ref, *, place=False):
        def work(cancel, progress):
            progress("Analyse des transitoires", ref.path, None, None, "Estimation du tempo")
            rhythm = analyze_music(ref.path, cancel=cancel)
            draft = None
            marker_count = 0
            if place:
                with self.ctl.session.staged(cancel.raise_if_cancelled) as draft:
                    from ..timeline import Scope

                    length = max((c.end for c in draft.tl.clips if draft.tl.track(c.track).kind == "video"), default=0)
                    with draft.group("Musique et repères", "directive", Scope(0, length)):
                        clip_id = place_music(draft, ref.path, ref.duration or 0)
                        clip = draft.tl.clip(clip_id)
                        tx = beat_markers(draft.tl, rhythm, clip.start, clip.end)
                        if tx.ops:
                            draft.do(tx, "Repères musicaux")
                            marker_count = len(tx.ops)
            return rhythm, draft, marker_count

        def apply(result):
            rhythm, draft, count = result
            if draft is not None:
                self.ctl.session.adopt(draft)
                self.refresh()
            if rhythm.bpm is None:
                return "Musique posée. Tempo incertain." if place else "Tempo incertain."
            self._operation.log.appendPlainText(f"{len(rhythm.beats)} battements détectés dans {ref.title}.")
            summary = f"{ref.title} · tempo estimé {rhythm.bpm:.1f} BPM."
            return f"{summary} Musique posée, {count} repère(s)." if place else summary

        self._start_operation("Analyse musicale", work, apply)

    def selected_sequence(self) -> str | None:
        it = self.rushes.currentItem()
        return it.data(Qt.UserRole) if it else None

    def configure_condensed(self):
        from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("Film condensé · Balanced")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Durée libre · cochez les rushes à analyser"))
        media = QListWidget()
        for path in sorted(self.ctl.rushes):
            item = QListWidgetItem(path, media)
            item.setCheckState(Qt.Checked)
        layout.addWidget(media)
        sync = QCheckBox("Synchroniser les angles par l’audio")
        sync.setChecked(True)
        layout.addWidget(sync)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Lancer l’analyse")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(590, 420)
        if dialog.exec():
            chosen = frozenset(media.item(i).text() for i in range(media.count()) if media.item(i).checkState() == Qt.Checked)
            self.run_prompt("Monte-moi cette séquence", media_scope=MediaScope(chosen), synchronize=sync.isChecked())

    def run_prompt(self, text: str, *, media_scope=None, synchronize=True) -> bool:
        """Renvoie True si la demande est acceptée. Le montage se termine en arrière-plan."""
        if self._operation is not None and self._operation.running:
            return False
        text = text.strip()
        if not text:
            return False
        try:
            from ..auto.directives import parse_directive
            d = parse_directive(text)
            if d.kind == "build":
                section = self.selected_sequence() if media_scope is None else None

                def work(cancel, progress):
                    return self.ctl.prepare_auto_edit(text, section, cancel=cancel, on_progress=progress,
                                                      analyzer=self.analyzer, source_factory=self.sync_source_factory,
                                                      media_scope=media_scope, synchronize=synchronize,
                                                      balanced=BalancedThresholds() if media_scope is not None else None)

                def apply(result):
                    draft, rep = result
                    self.ctl.session.adopt(draft.session)
                    self.ctl.rushes = draft.rushes
                    groups = rep["groups"]
                    skipped = [f"{g['name']}: {', '.join(g['skipped'])}" for g in groups if g["skipped"]]
                    self.prompt.clear()
                    self.refresh()
                    if media_scope is not None:
                        self._operation.log.appendPlainText(
                            f"{rep['analyzed']} rushes analysés, {rep['used']} utilisés, {rep['segments']} segments, "
                            f"{rep['duration']:.1f} s.")
                    return (f"Montage posé : {len(groups)} groupe(s) multicam, {rep['plain']} plan(s) à plat. Ctrl+Z annule la génération."
                            + (" Angles écartés : " + " · ".join(skipped) if skipped else ""))

                return self._start_operation("Montage automatique", work, apply)
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
        def work(cancel, progress):
            progress("Vérification de la timeline", None, None, None, "Vérification des médias avant export")
            if self.exporter:
                return self.exporter(self.ctl, Path(path), resolution)

            def render_progress(i, total, media):
                if i == total and media == "assemblage":
                    progress("Mixage et assemblage", str(path), total, total + 1, "Assemblage du MP4")
                else:
                    progress("Encodage vidéo", media, i - 1, total + 1, f"Segment {i}/{total} — {media}")

            return self.ctl.export(path, resolution=resolution, on_progress=render_progress, cancel=cancel)

        def apply(report):
            warnings = getattr(report, "warnings", None) or []
            if warnings:
                self._operation.log.appendPlainText("\n".join(warnings))
            return f"Exporté : {path}" + (f" — {len(warnings)} avertissement(s)" if warnings else "")

        self._start_operation("Export MP4", work, apply)

    def closeEvent(self, e) -> None:
        if self._operation is not None and self._operation.running:
            self._show_operation()
            self._operation.cancel_or_close()
            e.ignore()
            return
        if self._preview_worker is not None:
            self._preview_worker.cancel.cancel()
            self._preview_worker.wait()
        self.pause()
        if self._program_player is not None:
            self._program_player.stop()
            self._program_player.setSource(QUrl())
        if self._playback_dir is not None:
            self._playback_dir.cleanup()
        self.peaks.close()
        super().closeEvent(e)


def default_player_factory(ctl: ProjectController, gid: str):
    """Lecteur multicam réel : un lecteur par angle (proxy s'il existe, sinon original), planifié par `MulticamPlayer`."""
    from ..player.controller import MulticamPlayer
    from ..player.qt_backend import configure_hw
    from ..player.scheduler import MediaInfo
    from ..probe import probe_cached
    from .multicam_view import MulticamView
    configure_hw()
    media = {}
    for a in ctl.tl.group(gid).angles:
        src = ctl.project.root / a.media
        i = probe_cached(src)
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
