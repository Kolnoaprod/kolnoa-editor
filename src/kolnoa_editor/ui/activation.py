"""Réveil de l'instance déjà lancée : un second lancement envoie « show » à la première puis se termine (jamais deux moteurs)."""

from __future__ import annotations

import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    return f"KolnoaEditor-{getpass.getuser()}"


def notify_running_instance(timeout_ms: int = 1500, name: str | None = None) -> bool:
    s = QLocalSocket()
    s.connectToServer(name or server_name())
    if not s.waitForConnected(timeout_ms):
        return False
    s.write(b"show\n")
    s.waitForBytesWritten(timeout_ms)
    s.disconnectFromServer()
    return True


class ActivationServer(QObject):
    show_requested = Signal()

    def __init__(self, name: str | None = None, parent=None):
        super().__init__(parent)
        self.name = name or server_name()
        QLocalServer.removeServer(self.name)               # reste d'un plantage précédent (le verrou de fichier garantit l'unicité)
        self.server = QLocalServer(self)
        self.ok = self.server.listen(self.name)
        self.server.newConnection.connect(self._conn)

    def _conn(self) -> None:
        sock = self.server.nextPendingConnection()
        if sock is None:
            return
        sock.readyRead.connect(lambda s=sock: (s.readAll(), self.show_requested.emit(), s.disconnectFromServer()))
