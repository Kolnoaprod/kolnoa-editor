"""Fenêtre d'application (Qt hors écran) : le même workflow que les tests du contrôleur, piloté par la fenêtre."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from kolnoa_editor.app import ProjectController
from kolnoa_editor.ui.app_window import AppWindow
from test_auto_e2e import rushes


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, tmp_path):
    root = tmp_path / "m"
    root.mkdir()
    c = ProjectController.open(root, tmp_path / "d")
    c.add_rushes(rushes())
    exported = []
    w = AppWindow(c, exporter=lambda ctl, p, r: exported.append((p, r)) or p, analyzer=lambda r, p: r.moments)
    w.exported = exported
    yield w
    w.close()


def wait_operation(win, timeout=10):
    end = time.monotonic() + timeout
    while win._operation.running and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)
    assert not win._operation.running
    win._operation.close()


def build(win):
    assert win.run_prompt("Monte-moi cette séquence")
    wait_operation(win)


def test_rush_list_and_build_and_undo(win):
    assert win.rushes.count() == 3
    build(win)
    assert win.ctl.tl.clips_on("V1") and "Montage posé" in win.status.text()
    win.undo()
    assert not win.ctl.tl.clips


def test_zone_in_out_drives_directive_and_marks(win):
    build(win)
    tl = win.ctl.tl
    fps = tl.fps
    before = {c.id: c.duration for c in tl.clips_on("V1")}
    win.tlv.set_playhead(10 * fps)
    win.set_in()
    win.tlv.set_playhead(30 * fps)
    win.set_out()
    assert "Zone : 10.0 s → 30.0 s" in win.zone.text() and (win.tlv.mark_in, win.tlv.mark_out) == (10 * fps, 30 * fps)
    assert win.run_prompt("raccourcis cette partie de 4 secondes")
    after = {c.id: c.duration for c in win.ctl.tl.clips_on("V1")}
    assert sum(before.values()) - sum(after.values()) >= 4 * fps - 2
    win.undo()
    assert {c.id: c.duration for c in win.ctl.tl.clips_on("V1")} == before


def test_selection_by_clicks_and_errors_shown(win):
    build(win)
    win.tlv.set_playhead(max(c.end for c in win.ctl.tl.clips_on("V1")) + 10)
    assert not win.run_prompt("raccourcis de moitié")                 # tête hors plan : refus
    assert "aucun plan" in win.status.text().lower()
    assert not win.run_prompt("fais-moi un café")
    ids = [c.id for c in win.ctl.tl.clips_on("V1")[:3]]
    win.tlv._select(ids)
    assert win.run_prompt("accélère le rythme")


def test_save_reopen_and_export(win, tmp_path):
    build(win)
    win.save()
    before = win.ctl.tl.canonical()
    again = ProjectController.open(tmp_path / "m", tmp_path / "d")
    assert again.tl.canonical() == before
    win.export(str(tmp_path / "film.mp4"), "1080p")
    wait_operation(win)
    assert win.exported == [(tmp_path / "film.mp4", "1080p")]


def test_multicam_player_slot_and_angle_switch(win, tmp_path):
    from PySide6.QtWidgets import QLabel
    from kolnoa_editor.timeline import Angle
    from kolnoa_editor.timeline.model import MulticamGroup
    from kolnoa_editor.timeline.ops import AddGroup, add_multicam_clip
    from dataclasses import asdict
    seeks = []

    class Stub(QLabel):
        class player:
            @staticmethod
            def seek(t): seeks.append(t)
    win.player_factory = lambda ctl, gid: Stub("lecteur")
    g = MulticamGroup("g001", "A", "g", [Angle("A", "a.mp4", offset=0.0, status="reference", method="reference", confidence=1.0),
                                         Angle("B", "b.mp4", offset=2.0, status="ok", method="audio", confidence=1.0),
                                         Angle("C", "c.mp4", offset=0.0, status="unsynced")])
    win.ctl.session.do(AddGroup(asdict(g)))
    win.ctl.session.do(add_multicam_clip(win.ctl.tl, "g001", "A", "V1", 0, 100, 5.0, clip_id="m1"))
    win.refresh()
    assert isinstance(win.player_view, Stub)                                          # lecteur affiché dès qu'un groupe existe
    win.tlv.set_playhead(50)
    assert seeks and abs(seeks[-1] - 50 / win.ctl.tl.fps) < 1e-9                       # la tête de lecture pilote le lecteur
    win.tlv._select(["m1"])
    assert win.angles_of_selection() == ["A", "B"]                                    # l'angle non synchronisé n'est pas proposé
    assert win.switch_angle("B")
    c = win.ctl.tl.clip("m1")
    assert c.angle == "B" and c.media == "b.mp4" and abs(c.src_in - 3.0) < 1e-6        # même instant de groupe (5 s) → 3 s dans B
    assert win.ctl.session.state_of("m1") == "manuel"
    assert not win.switch_angle("C")                                                  # refus clair, rien ne change
    win.undo()
    assert win.ctl.tl.clip("m1").angle == "A"


def test_explain_selection(win):
    from kolnoa_editor.timeline import Angle
    from kolnoa_editor.timeline.model import MulticamGroup
    from kolnoa_editor.timeline.ops import AddGroup, add_multicam_clip
    from dataclasses import asdict
    win.player_factory = lambda ctl, gid: None
    g = MulticamGroup("g001", "A", "g", [Angle("A", "a.mp4", status="reference", method="reference", confidence=1.0),
                                         Angle("B", "b.mp4", offset=1.0, status="ok", method="audio", confidence=1.0),
                                         Angle("C", "c.mp4", status="impossible", method="audio")])
    win.ctl.session.do(AddGroup(asdict(g)))
    op = add_multicam_clip(win.ctl.tl, "g001", "A", "V1", 0, 100, 5.0, clip_id="m1")
    op.clip["extra"] = {"angle_choice": {"motif": "meilleure qualité visuelle (1.00 contre 0.45)", "scores": {"A": 0.45, "B": 1.0}, "analyse": "visuelle"}}
    win.ctl.session.do(op, origin="auto", scope=__import__("kolnoa_editor.timeline", fromlist=["Scope"]).Scope(0, 100))
    win.refresh()
    win.tlv._select(["m1"])
    t = win.explain_selection()
    assert "meilleure qualité visuelle" in t and "A 0.45" in t and "alternatives fiables : B" in t and "écartés" in t and "C" in t
    win.switch_angle("B")
    assert "choisi à la main" in win.explain_selection()


def test_prompt_at_playhead_without_explicit_selection(win):
    build(win)
    before = {c.id: c.duration for c in win.ctl.tl.clips_on("V1")}
    first = win.ctl.tl.clips_on("V1")[0]
    win.tlv.set_playhead(first.start)
    assert not win.tlv.selection
    assert win.run_prompt("raccourcis de moitié")
    after = {c.id: c.duration for c in win.ctl.tl.clips_on("V1")}
    assert after[first.id] < before[first.id]
    assert all(after[cid] == duration for cid, duration in before.items() if cid != first.id)
    win.undo()
    assert {c.id: c.duration for c in win.ctl.tl.clips_on("V1")} == before


def test_cancel_during_generation_keeps_history_and_qt_responsive(win, tmp_path):
    from threading import Event
    from PySide6.QtCore import QTimer
    from kolnoa_editor.auto import RushInfo

    win.ctl.add_rushes([RushInfo("extra.mp4", 4)])
    entered = Event()
    release = Event()

    def analyze(rush, project):
        entered.set()
        assert release.wait(5)
        return []

    win.analyzer = analyze
    win.show()
    journal = win.ctl.session.journal_path
    before = journal.read_bytes()
    timeline = win.ctl.tl.canonical()
    assert win.run_prompt("Monte-moi cette séquence")
    assert entered.wait(2)
    dialog = win._operation
    dialog.showMinimized()
    QApplication.processEvents()
    assert win._task_button.isVisible()
    win._task_button.click()
    assert not dialog.isMinimized()
    assert not win.run_prompt("Monte-moi cette séquence")
    QTimer.singleShot(0, dialog.cancel_btn.click)
    QApplication.processEvents()
    assert dialog.worker.cancel.is_set()
    release.set()
    wait_operation(win)
    assert "annulée" in win.status.text()
    assert journal.read_bytes() == before
    assert win.ctl.tl.canonical() == timeline
    assert ProjectController.open(tmp_path / "m", tmp_path / "d").tl.canonical() == timeline


@pytest.mark.parametrize("count", [5, 10, 119])
def test_auto_progress_on_real_ffmpeg_inputs(app, tmp_path, count):
    import shutil

    from kolnoa_editor.ffmpeg import ffmpeg_bin, run
    from kolnoa_editor.auto import RushInfo

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg indisponible")
    root = tmp_path / "medias"
    root.mkdir()
    first = root / "rush-000.mp4"
    run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=25:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=600:duration=2", "-c:v", "libx264", "-c:a", "aac", str(first)])
    for i in range(1, count):
        shutil.copyfile(first, root / f"rush-{i:03}.mp4")

    ctl = ProjectController.open(root, tmp_path / "project")
    # Sans heures communes, le cas isole le débit de traitement mono, sans corrélation multicam.
    ctl.add_rushes([RushInfo(path.name, 2) for path in sorted(root.glob("*.mp4"))])
    window = AppWindow(ctl)
    window.show()
    assert window.run_prompt("Monte-moi cette séquence", classic=True)      # parcours souple historique ; le défaut est Balanced
    events = []
    window._operation.worker.progress.connect(lambda *args: events.append(args))
    wait_operation(window, timeout=120)
    assert window._operation.bar.value() == 100
    assert "Montage posé" in window.status.text()
    assert len(ctl.tl.clips_on("V1")) == count
    assert {event[1] for event in events if event[0].startswith("Analyse audio")} == set(ctl.rushes)
    work = [event[2] for event in events if event[2] is not None]
    assert work == sorted(work) and work[-1] == 2 * count
    ctl.undo()
    assert not ctl.tl.clips
    window.close()


def test_instance_lock_and_activation(app, tmp_path):
    from uuid import uuid4

    from kolnoa_editor.app.instance import InstanceLock
    from kolnoa_editor.ui.activation import ActivationServer, notify_running_instance

    first = InstanceLock(tmp_path)
    second = InstanceLock(tmp_path)
    assert first.acquire()
    try:
        assert not second.acquire()
        name = f"KolnoaTest-{uuid4().hex}"
        server = ActivationServer(name)
        observed = []
        server.show_requested.connect(lambda: observed.append(True))
        assert server.ok and notify_running_instance(name=name)
        end = time.monotonic() + 2
        while not observed and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)
        assert observed == [True]
    finally:
        first.release()
    assert second.acquire()
    second.release()


def test_default_build_is_balanced_with_soft_fallback(app, tmp_path):
    """Défaut du « Monte-moi » : Balanced + multicam. Sur des rushes très unis Balanced ne retient rien : repli souple signalé, pas d'échec."""
    import shutil

    from kolnoa_editor.ffmpeg import ffmpeg_bin, run
    from kolnoa_editor.auto import RushInfo

    root = tmp_path / "m"
    root.mkdir()
    run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=96x64:r=25:d=2", "-f", "lavfi", "-i", "sine=frequency=500:duration=2",
         "-c:v", "libx264", "-c:a", "aac", str(root / "a.mp4")])
    ctl = ProjectController.open(root, tmp_path / "p")
    ctl.add_rushes([RushInfo("a.mp4", 2)])
    w = AppWindow(ctl)
    w.show()
    assert w.run_prompt("Monte-moi cette séquence")
    wait_operation(w, timeout=120)
    assert "Montage posé" in w.status.text() and ctl.tl.clips
    w.close()
