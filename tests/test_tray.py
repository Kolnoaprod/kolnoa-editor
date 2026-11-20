"""Icône de notification + fermeture en arrière-plan + export en tâche de fond + réveil de l'instance existante (Qt hors écran, vrais fichiers ffmpeg)."""

import os
import subprocess
import time
from dataclasses import asdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from kolnoa_editor.app import ProjectController
from kolnoa_editor.app.autostart import Autostart, MemoryBackend
from kolnoa_editor.app.engine import ResidentEngine
from kolnoa_editor.ffmpeg import ffmpeg_bin
from kolnoa_editor.timeline.model import Clip
from kolnoa_editor.timeline.ops import AddClip
from kolnoa_editor.ui.activation import ActivationServer, notify_running_instance
from kolnoa_editor.ui.app_window import AppWindow
from kolnoa_editor.ui.tray import TrayController


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setQuitOnLastWindowClosed(False)
    return a


def pump(app, cond, timeout=60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def setup(app, tmp_path):
    root = tmp_path / "m"
    root.mkdir()
    for n, col in (("a.mp4", "red"), ("b.mp4", "blue")):
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={col}:s=320x180:r=25:d=3", "-f", "lavfi", "-i",
                        "sine=f=440:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(root / n)], check=True)
    ctl = ProjectController.open(root, tmp_path / "d")
    ctl.import_rushes()
    fps = ctl.tl.fps
    ctl.session.do(AddClip(asdict(Clip("c1", "V1", "a.mp4", 0, 2 * fps, 0.0))), "pose")
    ctl.session.do(AddClip(asdict(Clip("c2", "V1", "b.mp4", 2 * fps, 2 * fps, 0.0))), "pose")
    eng = ResidentEngine(tmp_path / "eng", Autostart(MemoryBackend()))
    eng.start()
    win = AppWindow(ctl, player_factory=lambda c, g: None)
    win.engine = eng
    tray = TrayController(app, eng, win)
    tray.available = True                                   # le bac à sable hors écran n'a pas de zone de notification : on teste la logique
    win.tray = tray
    win.show()
    yield win, tray, eng, tmp_path
    tray.quitting = True
    eng.shutdown()
    win.close()


def test_close_hides_window_and_keeps_engine_and_jobs(setup, app):
    win, tray, eng, _ = setup

    def slow(j):
        while True:
            time.sleep(0.01)
            j.cancel.raise_if_cancelled()
    job = eng.jobs.run("analyse", "analyse longue", slow)
    pump(app, lambda: job.state == "running", 5)
    win.close()
    app.processEvents()
    assert not win.isVisible() and eng.lock.held             # masquée, moteur vivant
    assert job.state == "running"                            # la tâche continue fenêtre masquée
    assert eng.settings.close_notice_shown                   # l'information n'est donnée qu'une fois
    tray.show_window()
    assert win.isVisible()


def test_close_really_closes_when_setting_off(setup, app):
    win, tray, eng, _ = setup
    eng.update_settings(keep_running_on_close=False)
    win.close()
    app.processEvents()
    assert not win.isVisible() and not tray.quitting


def test_jobs_menu_lists_active_and_cancels(setup, app):
    win, tray, eng, _ = setup
    tray.refresh_jobs_menu()
    assert [a.text() for a in tray.jobs_menu.actions()] == ["Aucune tâche en cours"]

    def slow(j):
        while True:
            time.sleep(0.01)
            j.cancel.raise_if_cancelled()
    job = eng.jobs.run("export", "Export film.mp4", slow)
    pump(app, lambda: job.state == "running", 5)
    eng.jobs.update(job, progress=0.45)
    tray.refresh_jobs_menu()
    sub = tray.jobs_menu.actions()[0]
    assert "Export film.mp4" in sub.text() and "45 %" in sub.text()
    sub.menu().actions()[0].trigger()                        # « Annuler »
    assert pump(app, lambda: job.state == "cancelled", 5)


def test_quit_stops_everything_and_asks_when_jobs_active(setup, app, monkeypatch):
    win, tray, eng, _ = setup
    from PySide6.QtWidgets import QMessageBox

    def slow(j):
        while True:
            time.sleep(0.01)
            j.cancel.raise_if_cancelled()
    job = eng.jobs.run("export", "long", slow)
    pump(app, lambda: job.state == "running", 5)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    assert tray.quit() is False and eng.lock.held and job.state == "running"       # refus : rien n'est arrêté
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    assert tray.quit() is True
    assert job.state == "cancelled" and not eng.lock.held and tray.quitting


def test_export_runs_as_background_job_even_if_window_hidden(setup, app):
    win, tray, eng, tmp = setup
    out = tmp / "film.mp4"
    win.export(str(out), "1080p")
    win.close()                                              # masquée pendant le rendu
    assert not win.isVisible()
    assert pump(app, lambda: not eng.jobs.active() and eng.jobs.jobs(), 120)
    j = eng.jobs.jobs()[0]
    assert j.kind == "export" and j.state == "done" and j.progress == 1.0
    assert out.is_file() and out.stat().st_size > 1000
    pump(app, lambda: "Exporté" in win.status.text(), 5)
    assert "Exporté" in win.status.text()


def test_second_launch_wakes_first_instance(app):
    name = f"KolnoaEditorTest-{os.getpid()}"
    srv = ActivationServer(name)
    assert srv.ok
    got = []
    srv.show_requested.connect(lambda: got.append(1))
    assert notify_running_instance(1500, name) is True
    assert pump(app, lambda: got, 5)
    srv.server.close()
    assert notify_running_instance(300, name) is False        # personne n'écoute → l'appelant sait qu'il n'y a pas d'instance vivante
