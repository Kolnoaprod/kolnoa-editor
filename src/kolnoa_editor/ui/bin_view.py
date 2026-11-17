"""Panneau du chutier : liste des plans repérés (I/O), à glisser vers la timeline. Lecture seule du modèle."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem

from ..timeline.session import Session
from . import dnd


class BinView(QListWidget):
    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDefaultDropAction(Qt.CopyAction)
        self.refresh()

    def refresh(self) -> None:
        self.clear()
        for b in self.session.tl.bin:
            it = QListWidgetItem(f"{b.name}   ·   {b.length:.1f} s   ·   {b.kind}")
            it.setData(Qt.UserRole, b.id)
            self.addItem(it)

    def payload_for(self, item) -> dict:
        return {"sub": item.data(Qt.UserRole)}

    def startDrag(self, actions) -> None:
        it = self.currentItem()
        if it is None:
            return
        drag = QDrag(self)
        drag.setMimeData(dnd.encode(self.payload_for(it)))
        drag.exec(Qt.CopyAction)
