"""Ressources de marque (logo, icône) : dossier du paquet, ou dossier extrait par PyInstaller (même chemin relatif)."""

from __future__ import annotations

from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def asset(name: str) -> Path:
    return ASSETS / name
