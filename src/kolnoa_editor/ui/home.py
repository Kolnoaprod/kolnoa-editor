"""Écran d'accueil : tous les projets en cours, « Nouveau projet », reprise depuis l'historique (façon DaVinci Resolve / Premiere)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from ..app.catalog import ProjectCatalog
from .assets import asset


def _when(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return d.strftime("%d/%m/%Y %H:%M")


class HomeDialog(QDialog):
    def __init__(self, catalog: ProjectCatalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.result_path: Path | None = None
        self.setWindowTitle("Kolnoa Editor — Projets")
        self.setWindowIcon(QIcon(str(asset("kolnoa.ico"))))
        self.resize(980, 620)
        self.setObjectName("home")

        logo = QLabel()
        pm = QPixmap(str(asset("logo.png")))
        if not pm.isNull():
            logo.setPixmap(pm.scaledToHeight(40, Qt.SmoothTransformation))
        sub = QLabel("ÉDITEUR")
        sub.setObjectName("logoSub")
        head = QHBoxLayout()
        head.addWidget(logo)
        head.addSpacing(10)
        head.addWidget(sub)
        head.addStretch(1)

        title = QLabel("Projets")
        title.setObjectName("brand")
        title.setStyleSheet("font-size: 22px;")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Nom du nouveau projet")
        self.name.setText(f"Projet {datetime.now():%Y-%m-%d}")
        self.name.returnPressed.connect(self.create_new)
        new = QPushButton("＋  Nouveau projet")
        new.setObjectName("primary")
        new.clicked.connect(self.create_new)
        opn = QPushButton("Ouvrir un projet (.klnprj)…")
        opn.setObjectName("chip")
        opn.clicked.connect(self.open_file)
        fld = QPushButton("Ouvrir un dossier de rushes…")
        fld.setObjectName("chip")
        fld.clicked.connect(self.open_folder)
        hint = QLabel("Un nouveau projet démarre vide : vous glissez ensuite vos rushes dans la fenêtre.\nVos rushes ne sont jamais déplacés ni modifiés.")
        hint.setWordWrap(True)
        hint.setObjectName("zone")
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(QLabel("Nouveau"))
        left.addWidget(self.name)
        left.addWidget(new)
        left.addSpacing(16)
        left.addWidget(QLabel("Ouvrir"))
        left.addWidget(opn)
        left.addWidget(fld)
        left.addStretch(1)
        left.addWidget(hint)
        lw = QWidget()
        lw.setObjectName("panel")
        lw.setLayout(left)
        lw.setFixedWidth(300)

        self.list = QListWidget()
        self.list.setSpacing(2)
        self.list.itemDoubleClicked.connect(lambda it: self.accept_path(it.data(Qt.UserRole)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.empty = QLabel("Aucun projet pour l'instant.\nCréez-en un à gauche, puis glissez vos rushes.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("zone")
        right = QVBoxLayout()
        right.addWidget(title)
        right.addWidget(QLabel("Récents et en cours — double-clic pour ouvrir"))
        right.addWidget(self.list, 1)
        right.addWidget(self.empty, 1)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(lw)
        body.addLayout(right, 1)
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#38383f;")
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 18)
        root.addLayout(head)
        root.addWidget(sep)
        root.addLayout(body, 1)
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        entries = self.catalog.entries()
        for e in entries:
            if e["kind"] == "projet":
                sub = f"{e['rushes']} rush(es) · {e['sequences']} séquence(s) · {_when(e['when'])}"
            else:
                sub = f"Dossier de rushes · {_when(e['when'])}"
            it = QListWidgetItem(f"{e['name']}\n{sub}\n{e['path']}")
            it.setData(Qt.UserRole, e["path"])
            it.setSizeHint(QSize(100, 64))
            self.list.addItem(it)
        self.list.setVisible(bool(entries))
        self.empty.setVisible(not entries)
        if entries:
            self.list.setCurrentRow(0)

    def accept_path(self, path) -> None:
        self.result_path = Path(path)
        self.accept()

    def create_new(self) -> None:
        try:
            self.accept_path(self.catalog.create(self.name.text()))
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Nouveau projet", str(e))

    def open_file(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Ouvrir un projet Kolnoa", str(self.catalog.base), "Projets Kolnoa (*.klnprj)")
        if p:
            self.accept_path(p)

    def open_folder(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "Dossier de rushes")
        if p:
            self.accept_path(p)

    def _menu(self, pos) -> None:
        it = self.list.itemAt(pos)
        if not it:
            return
        m = QMenu(self)
        a_open = m.addAction("Ouvrir")
        a_forget = m.addAction("Retirer de la liste (rien n'est supprimé)")
        act = m.exec(self.list.viewport().mapToGlobal(pos))
        if act is a_open:
            self.accept_path(it.data(Qt.UserRole))
        elif act is a_forget:
            self.catalog.forget(it.data(Qt.UserRole))
            self.reload()
