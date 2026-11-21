"""Parcours livrable, de bout en bout, hors écran, sur de vrais fichiers FFmpeg synthétiques :
accueil → nouveau projet → import (glisser-déposer) → Monte-moi → enregistrement .klnprj → réouverture → export MP4.
Ce test ne remplace PAS un essai réel sous Windows avec de vrais rushes."""

import os
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from kolnoa_editor.app import ProjectController
from kolnoa_editor.app.catalog import ProjectCatalog
from kolnoa_editor.app.media_manifest import MediaManifest
from kolnoa_editor.ffmpeg import ffmpeg_bin
from kolnoa_editor.probe import probe
from kolnoa_editor.ui.app_window import AppWindow
from test_app_window import wait_operation


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _rush(path, src, secs=4):
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", f"{src}=size=320x180:rate=25:duration={secs}", "-f", "lavfi", "-i",
                    f"sine=f=440:d={secs}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def test_full_journey(app, tmp_path):
    rushes = tmp_path / "mes rushes"
    rushes.mkdir()
    _rush(rushes / "C0001.mp4", "testsrc2")
    _rush(rushes / "C0002.mp4", "smptebars")

    cat = ProjectCatalog(tmp_path / "Kolnoa_Projets")                       # 1-2. nouveau projet depuis l'accueil
    p = cat.create("Mariage test")
    m = MediaManifest(p)
    ctl = ProjectController.open(m.root, m.data_dir)
    assert not ctl.rushes
    win = AppWindow(ctl, manifest=m)
    win.show()

    win.import_references([str(rushes)], recursive=True)                    # 3. glisser-déposer du dossier
    wait_operation(win, timeout=120)
    assert len(ctl.rushes) == 2 and ctl.tl.fps in (25, 50)

    assert win.run_prompt("Monte-moi cette séquence")                       # 4. montage auto, défauts
    wait_operation(win, timeout=180)
    assert "Montage posé" in win.status.text() and ctl.tl.clips

    win.media_manifest.save()                                               # 6. .klnprj
    assert len(MediaManifest(p).media) == 2

    out = tmp_path / "film.mp4"                                             # 7. export
    win.export(str(out), "1080p")
    wait_operation(win, timeout=240)
    info = probe(str(out))
    assert info.has_audio and info.duration > 1
    assert [r for r in rushes.iterdir()] and all((rushes / n).stat().st_size > 0 for n in ("C0001.mp4", "C0002.mp4"))   # originaux intacts
    win.close()

    again = MediaManifest(p)                                                # réouverture du projet par l'accueil
    ctl2 = ProjectController.open(again.root, again.data_dir)
    assert ctl2.tl.clips and len(again.media) == 2                           # le montage et les références sont revenus
