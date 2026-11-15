"""Outils de test partagés : génération de médias synthétiques."""

import shutil
import subprocess
from pathlib import Path

from kolnoa_editor.ffmpeg import ffmpeg_bin


def ff(*args):
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", *args], check=True)


def copy_wedding(src: Path, dst: Path) -> Path:
    """Copie légère du faux mariage (mtime conservés) pour les tests qui modifient le dossier."""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".kolnoa"))
    return dst
