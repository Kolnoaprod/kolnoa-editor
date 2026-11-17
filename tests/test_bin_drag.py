"""Glisser depuis le chutier : aperçu avant dépôt, pistes verrouillées, insertion/recouvrement, rush brut, une seule annulation."""

import os
import time

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


# --- revue #22 : essai limité aux pistes concernées, cache, revalidation au dépôt, perf sur gros montages ---------
import random



def test_scoped_copy_is_independent_and_keeps_cross_track_rules(qapp, tmp_path):
    v = make(tmp_path)
    tl = v.tl
    sc = tl.scoped_copy(["V1"])
    assert {c.track for c in sc.clips} == {"V1"} and len(sc.tracks) == len(tl.tracks)
    sc.clip("c1").start = 999
    sc.clip("c1").extra["x"] = 1
    assert tl.clip("c1").start == 100 and "x" not in tl.clip("c1").extra          # copie indépendante
    tl.track("V2").locked = True
    assert {c.track for c in tl.scoped_copy(["V1"]).clips} == {"V1", "V2"}        # une piste verrouillée garde ses plans (règles de ripple)


def _rand_drop_plans(v, rnd, n):
    out = []
    for _ in range(n):
        v.drop_mode = rnd.choice(["overwrite", "insert"])
        v.drop_with_audio = rnd.random() < 0.4
        tr = rnd.choice(["V1", "V2", "A1", "A2"])
        x, y = pos(v, tr, rnd.randint(0, 600))
        out.append(((tr, round(v.vp.frame_of(x)), v.drop_mode, v.drop_with_audio), x, y))
    return out


def test_scoped_preview_verdict_equals_full_copy_verdict_on_random_drops(qapp, tmp_path):
    rnd = random.Random(7)
    for variant in range(4):
        v = make(tmp_path / f"v{variant}")
        if variant == 1:
            v.tl.track("A1").locked = True
        if variant == 2:
            v.tl.clip("m1").locked = True
        if variant == 3:
            v.tl.track("V2").locked = True
        for payload in ({"sub": "b001"}, {"sub": "b002"}, {"media": "r/c.mp4", "length": 6.0, "src_in": 1.0, "kind": "video"}):
            for key, x, y in _rand_drop_plans(v, rnd, 60):
                fast = v.plan_drop(payload, x, y)
                exact = v.plan_drop(payload, x, y, exact=True)
                assert fast["ok"] == exact["ok"], (variant, payload, key, fast["reason"], exact["reason"])
                if fast["ok"]:                                                   # même résultat de montage
                    a, b = v.tl.copy(), v.tl.copy()
                    execute(a, fast["tx"]); execute(b, exact["tx"])
                    assert a.canonical() == b.canonical()


def test_equivalent_validations_are_cached_and_model_change_invalidates(qapp, tmp_path):
    v = make(tmp_path)
    x, y = pos(v, "V2", 400)
    v.plan_drop({"sub": "b001"}, x, y)
    h0 = v.stats.get("drop_cache_hits", 0)
    for _ in range(5):
        v.plan_drop({"sub": "b001"}, x + 0.2, y + 1)                       # même image/piste/mode : même verdict, pas de nouvel essai
    assert v.stats["drop_cache_hits"] - h0 == 5
    v.plan_drop({"sub": "b001"}, x + 40, y)                                # autre image : nouvel essai
    assert v.stats["drop_cache_hits"] - h0 == 5
    v.session.do(v.plan_drop({"sub": "b001"}, x, y, exact=True)["tx"], "dépôt")
    v.refresh_model()                                                      # le modèle a changé : le cache est jeté
    assert v._drop_cache == {}


def test_drop_always_revalidates_exactly_even_if_the_preview_cache_is_stale(qapp, tmp_path):
    v = make(tmp_path)
    v.show()
    mime = dnd.encode({"sub": "b001"})
    x, y = pos(v, "V2", 300)
    p = QPoint(int(x), int(y))
    qapp.sendEvent(v, QDragEnterEvent(p, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    qapp.sendEvent(v, QDragMoveEvent(p, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    assert v._drop["ok"]
    v.tl.track("V2").locked = True                                         # le modèle change SANS refresh_model : l'aperçu en cache est périmé
    before = len(v.tl.clips)
    msgs = []
    v.rejected.connect(msgs.append)
    qapp.sendEvent(v, QDropEvent(QPointF(p), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    assert len(v.tl.clips) == before and msgs and "verrouill" in msgs[0]    # le dépôt revalide : refus, rien n'est écrit


def _big(tmp_path, n, ntracks=20):
    tl = Timeline(title="T", fps=FPS, tracks=[Track(f"V{i}", "video") for i in range(ntracks // 2)] +
                  [Track(f"A{i}", "audio") for i in range(ntracks // 2)])
    per = n // ntracks
    for ti, t in enumerate(tl.tracks):
        for i in range(per):
            tl.clips.append(Clip(f"c{ti}_{i}", t.id, "r/a.mp4", i * 130, 100, src_in=float(i % 50)))
    execute(tl, mark_subclip(tl, "r/b.mp4", 5.0, 9.0, "E", source_len=60.0))
    s = Session.create(tmp_path / f"big{n}", tl)
    v = TimelineView(s, source_len=lambda m: 100.0)
    v.resize(1400, v.rows.total_h + 4)
    v.vp.zoom = 1.0
    return v


@pytest.mark.parametrize("n", [5000, 10000])
def test_plan_drop_p95_on_large_projects(qapp, tmp_path, n):
    v = _big(tmp_path, n)
    ts = []
    for i in range(80):                                                    # 80 positions DIFFÉRENTES : aucun coup de cache
        x, y = 200 + i * 11, v.rows.y["V0" if i % 2 else "V3"] + 10
        t0 = time.perf_counter()
        r = v.plan_drop({"sub": "b001"}, x, y)
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    med, p95 = ts[len(ts) // 2], ts[int(len(ts) * .95)]
    print(f"[perf] plan_drop {n} plans : médiane {med:.1f} ms, p95 {p95:.1f} ms, max {ts[-1]:.1f} ms")
    assert r["ok"] and p95 < 40                                            # avant correction : ~160 ms (5 000) et ~360 ms (10 000)
