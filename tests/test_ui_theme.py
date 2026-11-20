"""Habillage V0.1.2 : thème appliqué une fois, structure de la fenêtre, timeline entièrement visible à l'ouverture."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QPushButton

from kolnoa_editor.app import ProjectController
from kolnoa_editor.ui import theme
from kolnoa_editor.ui.app_window import AppWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_theme_is_applied_once_and_dark(app):
    theme.apply_theme(app)
    qss = app.styleSheet()
    theme.apply_theme(app)
    assert app.styleSheet() == qss and theme.ACCENT in qss and app.property("kolnoa_theme")


def test_window_structure_and_primary_actions(app, tmp_path):
    root = tmp_path / "Projet"
    root.mkdir()
    ctl = ProjectController.open(root, tmp_path / "d")
    w = AppWindow(ctl, player_factory=lambda c, g: None)
    w.show()
    app.processEvents()
    names = {b.text(): b.objectName() for b in w.findChildren(QPushButton)}
    assert names["Exporter en MP4"] == "primary" and names["Envoyer"] == "primary"
    assert w.prompt.objectName() == "prompt" and w.play_btn.objectName() == "play"
    assert w.project_label.text() == "Projet"
    assert w.statusBar().isVisible()
    w.close()


def test_timeline_fits_the_whole_montage_when_opened(app, tmp_path):
    import subprocess
    from kolnoa_editor.ffmpeg import ffmpeg_bin
    from kolnoa_editor.auto import StyleParams
    root = tmp_path / "m"
    root.mkdir()
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=8", "-f", "lavfi", "-i", "sine=d=8",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(root / "a.mp4")], check=True)
    ctl = ProjectController.open(root, tmp_path / "d")
    ctl.import_rushes()
    ctl.build("Monte-moi cette séquence", None, StyleParams(shot_min=1, shot_max=3))
    w = AppWindow(ctl, player_factory=lambda c, g: None)
    w.resize(1400, 800)
    w.show()
    for _ in range(10):
        app.processEvents()
    assert w.tlv.vp.last_frame >= ctl.tl.end_frame * 0.98 and w.tlv.vp.zoom > 0.5   # tout le film visible, pas une miette à gauche
    w.close()
