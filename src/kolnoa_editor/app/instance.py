"""Une seule instance du moteur par session utilisateur (verrou de fichier, relâché par le système même en cas de plantage)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def engine_dir() -> Path:
    """Dossier des données du moteur résident (réglages, journal des tâches, verrou). Jamais dans un dossier de rushes."""
    env = os.environ.get("KOLNOA_ENGINE_DIR")
    if env:
        return Path(env)
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    return Path(base or Path.home() / ".config") / "KolnoaEditor"


class InstanceLock:
    def __init__(self, directory: Path):
        self.path = Path(directory) / "engine.lock"
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode())
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()

    @property
    def held(self) -> bool:
        return self._fh is not None
