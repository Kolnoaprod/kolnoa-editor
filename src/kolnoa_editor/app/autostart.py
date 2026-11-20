"""Démarrage avec Windows : clé `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` (par utilisateur, sans droits élevés)."""

from __future__ import annotations

import sys
from typing import Protocol

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "KolnoaEditor"


class Backend(Protocol):
    def get(self) -> str | None: ...
    def set(self, command: str) -> None: ...
    def delete(self) -> None: ...


class WinRegistryBackend:
    def get(self) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                return winreg.QueryValueEx(k, VALUE)[0]
        except FileNotFoundError:
            return None

    def set(self, command: str) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, VALUE, 0, winreg.REG_SZ, command)

    def delete(self) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, VALUE)
        except FileNotFoundError:
            pass


class MemoryBackend:
    """Substitut pour les plates-formes sans registre (tests, Linux) : mêmes règles, sans toucher au système."""
    def __init__(self):
        self.value: str | None = None

    def get(self):
        return self.value

    def set(self, command):
        self.value = command

    def delete(self):
        self.value = None


def default_command(minimized: bool = True) -> str:
    """Commande lancée à l'ouverture de session. Exécutable figé (PyInstaller) : lui-même ; sinon l'interpréteur + le lanceur."""
    if getattr(sys, "frozen", False):
        base = f'"{sys.executable}"'
    else:
        from pathlib import Path
        base = f'"{sys.executable}" "{Path(__file__).resolve().parents[3] / "tools" / "winapp" / "kolnoa_editor_app.py"}"'
    return base + (" --minimized" if minimized else "")


class Autostart:
    def __init__(self, backend: Backend | None = None):
        self.backend = backend or (WinRegistryBackend() if sys.platform == "win32" else MemoryBackend())

    def is_enabled(self) -> bool:
        return bool(self.backend.get())

    def enable(self, minimized: bool = True) -> str:
        cmd = default_command(minimized)
        self.backend.set(cmd)
        return cmd

    def disable(self) -> None:
        self.backend.delete()

    def apply(self, enabled: bool, minimized: bool = True) -> None:
        self.enable(minimized) if enabled else self.disable()
