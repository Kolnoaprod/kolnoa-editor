"""Fenêtre d'application (Qt hors écran) : le même workflow que les tests du contrôleur, piloté par la fenêtre."""

import os

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


def test_rush_list_and_build_and_undo(win):
    assert win.rushes.count() == 3
    assert win.run_prompt("Monte-moi cette séquence")
    assert win.ctl.tl.clips_on("V1") and "Montage posé" in win.status.text()
    win.undo()
    assert not win.ctl.tl.clips


def test_zone_in_out_drives_directive_and_marks(win):
    win.run_prompt("Monte-moi cette séquence")
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
    win.run_prompt("Monte-moi cette séquence")
    assert not win.run_prompt("raccourcis de moitié")                 # rien de sélectionné : message, pas d'exception
    assert "sélectionnez" in win.status.text().lower()
    assert not win.run_prompt("fais-moi un café")
    ids = [c.id for c in win.ctl.tl.clips_on("V1")[:3]]
    win.tlv._select(ids)
    assert win.run_prompt("accélère le rythme")


def test_save_reopen_and_export(win, tmp_path):
    win.run_prompt("Monte-moi cette séquence")
    win.save()
    before = win.ctl.tl.canonical()
    again = ProjectController.open(tmp_path / "m", tmp_path / "d")
    assert again.tl.canonical() == before
    win.export(str(tmp_path / "film.mp4"), "1080p")
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
