"""Médiathèque : rushes en vignettes ou en liste, miniatures calculées en arrière-plan, double-clic = ouvrir dans le moniteur Source,
glisser vers la timeline (charge `dnd` existante : un rush brut). Ne modifie jamais un rush ; les miniatures vont dans le dossier de données du projet."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QInputDialog, QMenu, QMessageBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from ..ffmpeg import ffmpeg_bin
from . import dnd

THUMB_W, THUMB_H = 224, 126


def fmt_duration(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class ThumbService(QObject):
    """Une miniature par rush (image à ~15 % de la durée), JPEG en cache disque ; 2 ffmpeg au plus ; jamais dans le fil d'interface."""
    ready = Signal(str)                                         # media

    def __init__(self, media_root: Path, cache_dir: Path, parent=None):
        super().__init__(parent)
        self.root, self.dir = Path(media_root), Path(cache_dir)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kolnoa-thumb")
        self._pending: set[str] = set()
        self._closed = False

    def path_for(self, media: str) -> Path:
        src = self.root / media
        try:
            st = src.stat()
            key = f"{media}|{st.st_size}|{int(st.st_mtime)}"
        except OSError:
            key = media
        return self.dir / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".jpg")

    def cached(self, media: str) -> Path | None:
        p = self.path_for(media)
        return p if p.is_file() and p.stat().st_size > 200 else None

    def want(self, media: str, duration: float) -> None:
        if self._closed or media in self._pending or self.cached(media):
            return
        self._pending.add(media)
        self.pool.submit(self._make, media, duration)

    def _make(self, media: str, duration: float) -> None:
        try:
            if self._closed:
                return
            out = self.path_for(media)
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".part.jpg")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            cmd = [ffmpeg_bin(), "-y", "-v", "error", "-ss", f"{max(0.0, duration * 0.15):.2f}", "-i", str(self.root / media), "-frames:v", "1",
                   "-vf", f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease,pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2:color=#0e1014",
                   "-q:v", "4", str(tmp)]
            subprocess.run(cmd, capture_output=True, timeout=60, creationflags=flags)
            if tmp.is_file() and tmp.stat().st_size > 200:
                tmp.replace(out)
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            self._pending.discard(media)
            if not self._closed:
                try:
                    self.ready.emit(media)
                except RuntimeError:                            # objet Qt déjà détruit à la fermeture
                    pass

    def close(self) -> None:
        self._closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)


class RushList(QListWidget):
    def startDrag(self, actions) -> None:
        it = self.currentItem()
        if it is None:
            return
        d = it.data(Qt.UserRole)
        drag = QDrag(self)
        drag.setMimeData(dnd.encode({"media": d["media"], "length": max(0.04, float(d["duration"])), "src_in": 0.0, "kind": "video",
                                     "name": Path(d["media"]).name}))
        drag.exec(Qt.CopyAction)


class MediaPanel(QWidget):
    openRequested = Signal(str)
    foldersChanged = Signal()                                 # double-clic sur un rush → moniteur Source

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.ctl = controller
        self.thumbs = ThumbService(controller.project.root, controller.project.data / "thumbs", self)
        self.thumbs.ready.connect(self._thumb_ready)
        self.mode = "grid"
        self._placeholder = self._ph()
        from ..app.bins import BinFolders
        self.bins = BinFolders(controller.project.data / "bins.json")
        self.folder_filter: str | None = None               # None = tous ; "" = non rangés ; sinon id de dossier virtuel

        self.btn_grid = QPushButton("▦  Vignettes")
        self.btn_list = QPushButton("☰  Liste")
        for b, name in ((self.btn_grid, "grid"), (self.btn_list, "list")):
            b.setCheckable(True)
            b.setObjectName("chip")
            b.clicked.connect(lambda _=False, n=name: self.set_mode(n))
        self.btn_grid.setChecked(True)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        grp.addButton(self.btn_grid)
        grp.addButton(self.btn_list)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrer les rushes…")
        self.search.textChanged.connect(self.populate)
        self.list = RushList()
        self.list.setDragEnabled(True)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setUniformItemSizes(True)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setSpacing(4)
        self.list.itemDoubleClicked.connect(lambda it: self.openRequested.emit(it.data(Qt.UserRole)["media"]))
        self.count = QLabel("")
        self.count.setObjectName("zone")

        self.folder_box = QComboBox()
        self.folder_box.setToolTip("Dossiers virtuels : pure organisation, vos rushes ne bougent pas sur le disque")
        self.folder_box.currentIndexChanged.connect(self._folder_picked)
        self.btn_new_folder = QPushButton("＋ Dossier")
        self.btn_new_folder.setObjectName("chip")
        self.btn_new_folder.clicked.connect(self.new_folder)
        self.btn_menu = QPushButton("⋯")
        self.btn_menu.setObjectName("chip")
        self.btn_menu.setToolTip("Renommer / supprimer le dossier sélectionné")
        self.btn_menu.clicked.connect(self._folder_menu)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self._fill_folders()
        frow = QHBoxLayout()
        frow.addWidget(self.folder_box, 1)
        frow.addWidget(self.btn_new_folder)
        frow.addWidget(self.btn_menu)
        top = QHBoxLayout()
        top.addWidget(self.btn_grid)
        top.addWidget(self.btn_list)
        top.addStretch(1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addLayout(frow)
        lay.addLayout(top)
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.count)
        self.set_mode("grid")

    # --- affichage ----------------------------------------------------------------------------------------
    def _ph(self) -> QIcon:
        pm = QPixmap(THUMB_W, THUMB_H)
        pm.fill(Qt.black)
        return QIcon(pm)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        grid = mode == "grid"
        self.btn_grid.setChecked(grid)
        self.btn_list.setChecked(not grid)
        self.list.setViewMode(QListView.IconMode if grid else QListView.ListMode)
        self.list.setIconSize(QSize(160, 90) if grid else QSize(72, 40))
        self.list.setGridSize(QSize(176, 134) if grid else QSize())
        self.list.setWordWrap(grid)
        self.list.setMovement(QListView.Static)
        self.list.setFlow(QListView.LeftToRight if grid else QListView.TopToBottom)
        self.populate()

    def _label(self, r) -> str:
        name = Path(r.media).name
        if self.mode == "grid":
            return f"{name}\n{fmt_duration(r.duration)}"
        folder = f"  ·  {r.group}" if r.group else ""
        return f"{name}   ·   {fmt_duration(r.duration)}{folder}"

    def populate(self, *_a, sequence: str | None = None) -> None:
        if sequence is not None:
            self._sequence = sequence
        seq = getattr(self, "_sequence", None)
        q = self.search.text().strip().lower()
        self.list.clear()
        n = 0
        for media, r in sorted(self.ctl.rushes.items(), key=lambda kv: (kv[1].start_time or "", kv[0])):
            if seq is not None and r.group != seq:
                continue
            if q and q not in media.lower():
                continue
            if self.folder_filter is not None and (self.bins.folder_of(media) or "") != self.folder_filter:
                continue
            it = QListWidgetItem(self._label(r))
            it.setData(Qt.UserRole, {"media": media, "duration": r.duration})
            it.setToolTip(f"{media}\n{fmt_duration(r.duration)}" + (f"\n{r.device}" if r.device else "") + "\nDouble-clic : ouvrir · Glisser : poser sur la timeline")
            t = self.thumbs.cached(media)
            it.setIcon(QIcon(str(t)) if t else self._placeholder)
            if self.mode == "grid":
                it.setSizeHint(QSize(172, 130))
                it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.list.addItem(it)
            if not t:
                self.thumbs.want(media, r.duration)
            n += 1
        self.count.setText(f"{n} rush(es)" + (f" · séquence « {seq or '(sans dossier)'} »" if seq is not None else ""))

    # --- dossiers virtuels ----------------------------------------------------------------------------------
    def _fill_folders(self) -> None:
        self.folder_box.blockSignals(True)
        self.folder_box.clear()
        self.folder_box.addItem("Tous les rushes", None)
        self.folder_box.addItem("Non rangés", "")
        for fid, name in self.bins.names().items():
            self.folder_box.addItem(f"📁  {name}", fid)
        i = self.folder_box.findData(self.folder_filter)
        self.folder_box.setCurrentIndex(max(0, i))
        self.folder_box.blockSignals(False)

    def _folder_picked(self, *_a) -> None:
        self.folder_filter = self.folder_box.currentData()
        self.populate()

    def new_folder(self, name: str | None = None) -> None:
        if not isinstance(name, str):
            name, ok = QInputDialog.getText(self, "Nouveau dossier", "Nom du dossier")
            if not ok:
                return
        try:
            fid = self.bins.create(name)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Dossier", str(e))
            return
        self.folder_filter = fid
        self._fill_folders()
        self.populate()
        self.foldersChanged.emit()

    def _folder_menu(self) -> None:
        fid = self.folder_box.currentData()
        if not fid:
            return
        menu = QMenu(self)
        a_ren = menu.addAction("Renommer…")
        a_del = menu.addAction("Supprimer le dossier (les rushes restent intacts)")
        act = menu.exec(self.btn_menu.mapToGlobal(self.btn_menu.rect().bottomLeft()))
        try:
            if act is a_ren:
                name, ok = QInputDialog.getText(self, "Renommer", "Nom du dossier", text=self.bins.names()[fid])
                if ok:
                    self.bins.rename(fid, name)
            elif act is a_del:
                self.bins.delete(fid)
                self.folder_filter = None
            else:
                return
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Dossier", str(e))
            return
        self._fill_folders()
        self.populate()
        self.foldersChanged.emit()

    def move_selected(self, fid: str | None) -> None:
        media = [it.data(Qt.UserRole)["media"] for it in self.list.selectedItems()]
        if media:
            self.bins.assign(media, fid)
            self.populate()

    def _context_menu(self, pos) -> None:
        if not self.list.selectedItems():
            return
        menu = QMenu(self)
        a_open = menu.addAction("Ouvrir dans Source")
        sub = menu.addMenu("Ranger dans")
        acts = {sub.addAction(f"📁  {name}"): fid for fid, name in self.bins.names().items()}
        a_new = sub.addAction("＋ Nouveau dossier…")
        a_out = menu.addAction("Sortir du dossier")
        act = menu.exec(self.list.viewport().mapToGlobal(pos))
        if act is a_open:
            self.openRequested.emit(self.list.selectedItems()[0].data(Qt.UserRole)["media"])
        elif act in acts:
            self.move_selected(acts[act])
        elif act is a_new:
            name, ok = QInputDialog.getText(self, "Nouveau dossier", "Nom du dossier")
            if ok:
                try:
                    self.move_selected(self.bins.create(name))
                    self._fill_folders()
                    self.foldersChanged.emit()
                except (ValueError, OSError) as e:
                    QMessageBox.warning(self, "Dossier", str(e))
        elif act is a_out:
            self.move_selected(None)

    def _thumb_ready(self, media: str) -> None:
        p = self.thumbs.cached(media)
        if not p:
            return
        icon = QIcon(str(p))
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole)["media"] == media:
                it.setIcon(icon)

    def close(self) -> bool:
        self.thumbs.close()
        return super().close()
