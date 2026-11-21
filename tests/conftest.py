"""Faux mariage généré avec ffmpeg : mêmes noms de fichiers que les vraies caméras."""

import os
import subprocess

import pytest

from kolnoa_editor.ffmpeg import ffmpeg_bin


def _ff(*args):
    subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", *args], check=True)


@pytest.fixture(scope="session")
def wedding(tmp_path_factory):
    root = tmp_path_factory.mktemp("MARIAGE TEST")
    (root / "MAIRIE" / "A7IV").mkdir(parents=True)
    (root / "MAIRIE" / "OP4").mkdir(parents=True)
    (root / "SOIREE" / "DRONE").mkdir(parents=True)
    (root / "SOIREE" / "CANON").mkdir(parents=True)
    (root / "SOIREE" / "AUDIO").mkdir(parents=True)

    # A7IV : 1280x720 25 fps, avec son (sinus 440 Hz), 6 s
    sony = root / "MAIRIE" / "A7IV" / "20261011_C4837.MP4"
    _ff("-f", "lavfi", "-i", "testsrc=s=1280x720:r=25:d=6",
        "-f", "lavfi", "-i", "sine=f=440:d=6", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(sony))
    os.utime(sony, (1791719199, 1791719199))

    # DJI sans son, 4:3 pour tester le pad, 5 s
    dji = root / "SOIREE" / "DRONE" / "DJI_20261108192622_0015_D.MP4"
    _ff("-f", "lavfi", "-i", "testsrc2=s=800x600:r=30:d=5", "-c:v", "libx264", "-an", str(dji))

    # Canon
    canon = root / "SOIREE" / "CANON" / "A_0009C238A261108_113335HL_CANON.MP4"
    _ff("-f", "lavfi", "-i", "smptebars=s=640x360:r=25:d=4",
        "-f", "lavfi", "-i", "sine=f=880:d=4", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(canon))

    # Musique + enregistreur audio
    _ff("-f", "lavfi", "-i", "sine=f=220:d=30", str(root / "SOIREE" / "AUDIO" / "4CH000I.wav"))
    _ff("-f", "lavfi", "-i", "sine=f=330:d=30", str(root / "musique.mp3"))
    (root / "notes.txt").write_text("ignoré")
    return root


@pytest.fixture
def mariage(wedding, tmp_path):
    """Copie modifiable du faux mariage."""
    from helpers import copy_wedding

    return copy_wedding(wedding, tmp_path / "mariage")


def pytest_sessionfinish(session, exitstatus):
    """Qt (QApplication/QMediaPlayer hors écran) plante à la destruction de l'interpréteur sous Linux APRÈS que tous les tests sont passés,
    ce qui rendait la CI rouge (code 139) sans qu'aucun test échoue. On sort proprement avec le vrai code de résultat."""
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and os.name == "posix" and os.environ.get("KOLNOA_NO_HARD_EXIT") != "1":
        import sys
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(int(exitstatus))
