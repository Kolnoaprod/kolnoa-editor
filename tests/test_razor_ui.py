"""Rasoir au clavier (S par défaut, configurable), annuler/rétablir, conservation des réglages du plan."""

import os

import numpy as np
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from kolnoa_editor.timeline import Clip, Timeline, Track
from kolnoa_editor.timeline.session import Session
from kolnoa_editor.ui.shortcuts import DEFAULTS, Shortcuts
from kolnoa_editor.ui.timeline_view import TimelineView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def base_tl():
    tl = Timeline(title="T", fps=25, tracks=[Track("V1", "video"), Track("V2", "video"), Track("A1", "audio")])
    tl.clips += [Clip("v", "V1", "r/a.mp4", 0, 200, src_in=3.0, fade_in=0.5, fade_out=0.6, gain_db=-3.0, note="plan large",
                      label="Houppa", link="L1", audio="mute"),
                 Clip("a", "A1", "r/a.mp4", 0, 200, src_in=3.0, gain_db=2.0, link="L1"),
                 Clip("w", "V2", "r/b.mp4", 50, 100, src_in=0.0)]
    tl.validate()
    return tl


@pytest.fixture
def view(qapp, tmp_path):
    s = Session.create(tmp_path / "s", base_tl())
    v = TimelineView(s)
    v.resize(1000, v.rows.total_h + 4)
    return v


def key(v, k, mod=Qt.NoModifier):
    QTest.keyClick(v, k, mod)


def state(v):
    d = v.tl.to_dict()
    d.pop("retired", None)            # ids réservés par l'annulation (réutilisés au rétablissement) : comptabilité interne
    return d


def clips(v):
    return sorted((c.track, c.start, c.end) for c in v.tl.clips)


def test_s_cuts_selected_clip_and_its_linked_audio_and_keeps_settings(view):
    view.set_playhead(80)
    view._select(["v"])
    key(view, Qt.Key_S)
    assert clips(view) == [("A1", 0, 80), ("A1", 80, 200), ("V1", 0, 80), ("V1", 80, 200), ("V2", 50, 150)]   # V2 non sélectionné : intact
    left, right = [c for c in view.tl.clips_on("V1")]
    assert left.fade_in == 0.5 and left.fade_out == 0.0 and right.fade_in == 0.0 and right.fade_out == 0.6
    for c in (left, right):
        assert (c.gain_db, c.note, c.label, c.media, c.audio) == (-3.0, "plan large", "Houppa", "r/a.mp4", "mute")
    assert right.src_in == pytest.approx(3.0 + 80 / 25) and right.parent == "v"
    arrs = view.tl.clips_on("A1")
    assert arrs[1].link == right.link and arrs[1].link != left.link and arrs[0].link == left.link   # les moitiés restent liées par paire
    assert arrs[0].gain_db == arrs[1].gain_db == 2.0


def test_without_selection_cuts_all_unlocked_tracks_and_spares_locked_ones(view):
    view.tl.track("V2").locked = True
    view.set_playhead(80)
    key(view, Qt.Key_S)
    assert ("V2", 50, 150) in clips(view) and ("V1", 0, 80) in clips(view)


def test_shift_s_cuts_all_tracks_even_with_a_selection(view):
    view.set_playhead(80)
    view._select(["v"])
    key(view, Qt.Key_S, Qt.ShiftModifier)
    assert ("V2", 50, 80) in clips(view)


def test_nothing_to_cut_is_refused_without_change(view):
    msgs, snap = [], view.tl.to_dict()
    view.rejected.connect(msgs.append)
    view.set_playhead(500)
    key(view, Qt.Key_S)
    assert msgs and view.tl.to_dict() == snap


def test_undo_redo_keys_are_reliable_across_several_cuts(view):
    snap0 = state(view)
    for f in (40, 80, 120):
        view.set_playhead(f)
        key(view, Qt.Key_S)
    snaps = [state(view)]
    n = len(view.tl.clips)
    key(view, Qt.Key_Z, Qt.ControlModifier)
    assert len(view.tl.clips) < n
    key(view, Qt.Key_Z, Qt.ControlModifier); key(view, Qt.Key_Z, Qt.ControlModifier)
    assert state(view) == snap0                      # trois coupes = trois annulations, retour exact
    key(view, Qt.Key_Z, Qt.ControlModifier)                # rien à annuler : pas d'exception, rien ne change
    assert state(view) == snap0
    for _ in range(3):
        key(view, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert state(view) == snaps[0]
    key(view, Qt.Key_Z, Qt.ControlModifier); key(view, Qt.Key_Y, Qt.ControlModifier)
    assert state(view) == snaps[0]                   # Ctrl+Y rétablit aussi


def test_razor_on_multicam_clip_keeps_group_and_angle(qapp, tmp_path):
    from tests.test_multicam_live import tl_cam
    s = Session.create(tmp_path / "s", tl_cam())
    v = TimelineView(s)
    v.set_playhead(150)
    key(v, Qt.Key_S)
    l, r = v.tl.clips_on("V1")
    assert (l.group, l.angle) == (r.group, r.angle) == ("g001", "A7") and r.src_in == pytest.approx(l.src_in + 50 / v.tl.fps)


# --- raccourcis configurables ---------------------------------------------------------------------------------
def test_shortcut_is_configurable_and_conflicts_are_refused(view):
    sc = Shortcuts()
    assert sc.map["razor"] == "S"
    sc.set("razor", "C")
    with pytest.raises(ValueError):
        sc.set("undo", "C")                                # déjà pris
    with pytest.raises(ValueError):
        sc.set("inconnu", "X")
    view.shortcuts = sc
    view.set_playhead(80)
    key(view, Qt.Key_S)                                    # l'ancienne touche ne fait plus rien
    assert len(view.tl.clips) == 3
    key(view, Qt.Key_C)
    assert len(view.tl.clips) == 6                         # V1, A1 (liés) et V2 coupés à 80


def test_shortcut_file_roundtrip_and_corrupt_file_falls_back_to_defaults(tmp_path):
    sc = Shortcuts()
    sc.set("razor", "X")
    f = tmp_path / "keys.json"
    sc.save(f)
    assert Shortcuts.load(f).map["razor"] == "X"
    f.write_text("{pas du json", encoding="utf-8")
    assert Shortcuts.load(f).map == Shortcuts().map
    f.write_text('{"razor": "Shift+S", "razor_all": "Shift+S", "bidon": "Z"}', encoding="utf-8")
    loaded = Shortcuts.load(f)                             # conflit : l'entrée fautive est ignorée, jamais d'exception
    assert loaded.map["razor_all"] == "Shift+S" and loaded.map["razor"] == "S"
    assert set(loaded.map) == set(DEFAULTS)
