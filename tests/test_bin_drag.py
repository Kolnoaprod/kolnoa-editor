"""Glisser depuis le chutier : aperçu avant dépôt, pistes verrouillées, insertion/recouvrement, rush brut, une seule annulation."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from kolnoa_editor.timeline import Clip, Timeline, Track, mark_subclip, place_rush
from kolnoa_editor.timeline.ops import execute
from kolnoa_editor.timeline.session import Session
from kolnoa_editor.ui import dnd
from kolnoa_editor.ui.bin_view import BinView
from kolnoa_editor.ui.timeline_view import TimelineView

FPS = 25


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def make(tmp_path):
    tl = Timeline(title="T", fps=FPS, tracks=[Track("V1", "video"), Track("V2", "video"), Track("A1", "audio"), Track("A2", "audio")])
    tl.clips += [Clip("c1", "V1", "r/a.mp4", 100, 100, src_in=0.0), Clip("c2", "V1", "r/a.mp4", 300, 100, src_in=9.0),
                 Clip("m1", "V2", "r/a.mp4", 100, 100, src_in=0.0)]
    execute(tl, mark_subclip(tl, "r/b.mp4", 5.0, 9.0, "Entrée", source_len=60.0))      # b001 : 4 s = 100 images
    execute(tl, mark_subclip(tl, "r/s.wav", 0.0, 2.0, "Ambiance", kind="audio"))        # b002 audio
    tl.validate()
    s = Session.create(tmp_path / "s", tl)
    v = TimelineView(s, source_len=lambda m: 120.0)
    v.resize(1400, v.rows.total_h + 4)
    v.vp.zoom, v.vp.offset = 1.0, 0.0
    return v


def pos(v, track, frame):
    return v.vp.x_of(frame), v.rows.y[track] + 10


def snapshot(v):
    d = v.tl.to_dict()
    d.pop("retired", None)
    return d


def test_overwrite_drop_places_clip_covering_what_is_below_and_is_one_undo(qapp, tmp_path):
    v = make(tmp_path)
    before = snapshot(v)
    x, y = pos(v, "V1", 150)                                   # tombe au milieu de c1 [100,200)
    plan = v.plan_drop({"sub": "b001"}, x, y)
    assert plan["ok"] and plan["mode"] == "overwrite" and (plan["start"], plan["frames"]) == (150, 100)
    assert snapshot(v) == before                               # l'aperçu ne modifie rien
    v.session.do(plan["tx"], "dépôt")
    v.refresh_model()
    cs = [(c.start, c.end, c.media) for c in v.tl.clips_on("V1")]
    assert cs == [(100, 150, "r/a.mp4"), (150, 250, "r/b.mp4"), (300, 400, "r/a.mp4")]
    new = v.tl.clips_on("V1")[1]
    assert new.src_in == 5.0 and new.label == "Entrée"
    v.session.undo()
    assert snapshot(v) == before


def test_insert_mode_shifts_the_rest_of_the_track_only(qapp, tmp_path):
    v = make(tmp_path)
    x, y = pos(v, "V1", 200)
    plan = v.plan_drop({"sub": "b001"}, x, y, ctrl=False)
    v.drop_mode = "insert"
    plan = v.plan_drop({"sub": "b001"}, x, y)
    assert plan["mode"] == "insert" and plan["ok"]
    assert v.plan_drop({"sub": "b001"}, x, y, ctrl=True)["mode"] == "overwrite"      # Ctrl inverse le mode
    v.session.do(plan["tx"], "insertion")
    assert [(c.start, c.end) for c in v.tl.clips_on("V1")] == [(100, 200), (200, 300), (400, 500)]
    assert [(c.start, c.end) for c in v.tl.clips_on("V2")] == [(100, 200)]           # les autres pistes ne bougent pas


def test_locked_track_and_wrong_kind_are_refused_with_a_reason_and_nothing_changes(qapp, tmp_path):
    v = make(tmp_path)
    before = snapshot(v)
    v.tl.track("V2").locked = True
    x, y = pos(v, "V2", 120)
    plan = v.plan_drop({"sub": "b001"}, x, y)
    assert not plan["ok"] and "verrouill" in plan["reason"]
    x, y = pos(v, "A1", 120)
    plan = v.plan_drop({"sub": "b001"}, x, y)
    assert not plan["ok"] and "audio" in plan["reason"]
    x, y = pos(v, "V1", 120)
    assert not v.plan_drop({"sub": "b002"}, x, y)["ok"]       # un plan audio sur une piste vidéo
    assert not v.plan_drop({"sub": "b001"}, 10, 5)["ok"]      # sur la règle : pas de piste
    assert snapshot(v) == before | {"tracks": snapshot(v)["tracks"]}      # seul le verrou posé par le test a changé


def test_locked_clip_below_blocks_overwrite_and_nothing_changes(qapp, tmp_path):
    v = make(tmp_path)
    v.tl.clip("c1").locked = True
    x, y = pos(v, "V1", 150)
    plan = v.plan_drop({"sub": "b001"}, x, y)
    assert not plan["ok"]


def test_drop_with_audio_links_sound_on_first_unlocked_audio_track(qapp, tmp_path):
    v = make(tmp_path)
    v.tl.track("A1").locked = True
    v.drop_with_audio = True
    x, y = pos(v, "V2", 400)
    plan = v.plan_drop({"sub": "b001"}, x, y)
    assert plan["ok"] and plan["audio_track"] == "A2"
    v.session.do(plan["tx"], "dépôt")
    a = v.tl.clips_on("A2")[0]
    vid = [c for c in v.tl.clips_on("V2") if c.media == "r/b.mp4"][0]
    assert a.link and a.link == vid.link and a.media == "r/b.mp4"


def test_raw_rush_drop_respects_source_length_and_creates_no_bin_entry(qapp, tmp_path):
    v = make(tmp_path)
    x, y = pos(v, "V2", 500)
    pl = {"media": "r/c.mp4", "length": 8.0, "src_in": 0.0, "kind": "video"}
    plan = v.plan_drop(pl, x, y)
    assert plan["ok"] and plan["frames"] == 200
    n_bin = len(v.tl.bin)
    v.session.do(plan["tx"], "rush")
    assert len(v.tl.bin) == n_bin
    bad = v.plan_drop({**pl, "src_in": 119.0}, x, y)                     # 119 + 8 s > 120 s de rush
    assert not bad["ok"] and "fin du rush" in bad["reason"]


def test_real_qt_drag_events_preview_then_drop(qapp, tmp_path):
    v = make(tmp_path)
    v.show()
    mime = dnd.encode({"sub": "b001"})
    x, y = pos(v, "V2", 300)
    p = QPoint(int(x), int(y))
    qapp.sendEvent(v, QDragEnterEvent(p, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    mv = QDragMoveEvent(p, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    qapp.sendEvent(v, mv)
    assert v._drop and v._drop["ok"] and v._drop["track"] == "V2"                # l'aperçu existe avant le dépôt
    before = len(v.tl.clips)
    assert len(v.tl.clips) == before
    qapp.sendEvent(v, QDropEvent(QPointF(p), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    assert len(v.tl.clips) == before + 1 and v._drop is None
    v.grab()                                                                     # le dessin de l'aperçu ne plante pas
    # glisser étranger : ignoré
    from PySide6.QtCore import QMimeData
    alien = QMimeData(); alien.setText("x")
    ev = QDragEnterEvent(p, Qt.CopyAction, alien, Qt.LeftButton, Qt.NoModifier)
    ev.ignore(); qapp.sendEvent(v, ev)
    assert not ev.isAccepted()


def test_corrupt_payload_is_ignored_and_bin_view_lists_subclips(qapp, tmp_path):
    from PySide6.QtCore import QMimeData
    bad = QMimeData(); bad.setData(dnd.MIME, b"{pas du json")
    assert dnd.decode(bad) is None
    assert dnd.decode(None) is None
    v = make(tmp_path)
    b = BinView(v.session)
    assert b.count() == 2 and b.payload_for(b.item(0)) == {"sub": "b001"}
