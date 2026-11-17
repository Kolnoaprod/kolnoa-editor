"""Interface Qt : géométrie sans écran, dessin hors écran, glisser = une étape d'annulation, refus du moteur, budget de dessin."""

import os
import time

import numpy as np
import pytest

from kolnoa_editor.timeline import Clip, Timeline, Track
from kolnoa_editor.timeline.session import Session
from kolnoa_editor.ui import layout as L
from kolnoa_editor.ui.peaks import PeakPyramid

FPS = 25


def make_tl(n_per_track=80, tracks=("V1", "V2", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8")):
    tl = Timeline(title="T", fps=FPS, tracks=[Track(t, "video" if t.startswith("V") else "audio") for t in tracks])
    k = 0
    for ti, t in enumerate(tracks):
        pos = 0
        for i in range(n_per_track):
            k += 1
            dur = 40 + (i * 7 + ti * 3) % 300
            tl.clips.append(Clip(f"c{k:04d}", t, "RUSH/a.mp4", pos, dur, src_in=float(i)))
            pos += dur + 10 + (i % 5)
    tl.validate()
    return tl


# --- sans Qt -------------------------------------------------------------------------------------------------
def test_viewport_zoom_keeps_the_frame_under_the_cursor():
    vp = L.Viewport(zoom=0.4, offset=100.0, width=1000)
    f = vp.frame_of(300)
    vp.zoom_at(300, 2.0)
    assert vp.frame_of(300) == pytest.approx(f)
    vp.zoom_at(300, 1e9)
    assert vp.zoom == L.MAX_ZOOM


def test_visible_clips_match_a_brute_force_scan():
    tl = make_tl()
    idx = L.ClipIndex(tl)
    for tid in ("V1", "A4"):
        for f0, f1 in ((0, 500), (3000, 4200), (9000, 9100), (-50, 10)):
            got = {c.id for c in idx.visible(tid, f0, f1)}
            want = {c.id for c in tl.clips if c.track == tid and c.end > f0 and c.start < f1}
            assert got == want


def test_hit_test_zones_and_ruler():
    tl = make_tl()
    rows, idx, vp = L.Rows(tl), L.ClipIndex(tl), L.Viewport(zoom=1.0, offset=0.0, width=1000)
    c = tl.clips[0]                                   # V1, début 0
    y = rows.y["V1"] + 5
    assert L.hit_test(idx, rows, vp, c.start + 60, y)[1] == "body"
    assert L.hit_test(idx, rows, vp, c.start + 2, y)[1] == "left"
    assert L.hit_test(idx, rows, vp, c.end - 2, y)[1] == "right"
    assert L.hit_test(idx, rows, vp, 50, 5) == (None, "none")           # la règle n'est pas un plan


def test_ruler_step_is_round_and_wide_enough():
    for z in (0.002, 0.05, 0.4, 4.0):
        step = L.ruler_step(z, FPS)
        assert step % FPS == 0 and step * z >= 70 or step == 3600 * FPS


def test_peak_pyramid_columns():
    base = np.zeros(1000, dtype=np.uint8)
    base[500] = 200                                    # un pic à t = 10 s
    p = PeakPyramid(base)
    fine = p.columns(9.9, 0.02, 20)
    assert fine.max() == 200 and fine[5] == 200
    coarse = p.columns(0.0, 1.0, 20)                   # 1 s par pixel : le pic reste visible (maximum, pas moyenne)
    assert coarse[10] == 200
    assert p.columns(1000, 1.0, 5).tolist() == [0] * 5


# --- avec Qt (hors écran) ------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp, tmp_path):
    from kolnoa_editor.ui.timeline_view import TimelineView
    s = Session.create(tmp_path / "sess", make_tl())
    pyr = PeakPyramid((np.abs(np.sin(np.arange(60 * 50) / 7.0)) * 200).astype(np.uint8))
    v = TimelineView(s, peaks_provider=lambda media: pyr)
    v.resize(1200, v.rows.total_h + 4)
    v.vp.zoom, v.vp.offset = 0.4, 0.0
    return v


def test_paint_draws_only_visible_clips_within_budget(view):
    view.grab()                                        # premier dessin (chauffe)
    t = time.perf_counter()
    for _ in range(10):
        view.grab()
    ms = (time.perf_counter() - t) * 100
    total = len(view.tl.clips)
    assert 0 < view.stats["clips_drawn"] < total / 2
    print(f"\n[perf] {view.stats['clips_drawn']}/{total} plans dessinés, {ms:.1f} ms par image complète (grab hors écran)")
    assert ms < 120                                    # garde-fou large : on mesure, on ne triche pas sur CI lente
    t = time.perf_counter()
    for i in range(20):                                # défilement : les formes d'onde déjà calculées sont réutilisées
        view.vp.pan_px(3)
        view.grab()
    pan_ms = (time.perf_counter() - t) * 50
    print(f"[perf] défilement : {pan_ms:.1f} ms par image")
    t = time.perf_counter()
    for i in range(10):                                # zoom : tout est recalculé à chaque pas (cas le plus lourd)
        view.vp.zoom_at(400, 1.05 if i % 2 else 0.95)
        view.grab()
    zoom_ms = (time.perf_counter() - t) * 100
    print(f"[perf] zoom : {zoom_ms:.1f} ms par image")
    assert pan_ms < 40 and zoom_ms < 60          # mesuré : ~5 et ~17 ms ; le zoom continu est surveillé (régression > ×3)


def test_drag_moves_one_clip_in_a_single_undo_step(view):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    tl, s = view.tl, view.session
    c = next(k for k in tl.clips if k.track == "V1" and k.start > 0)
    last = max(k.end for k in tl.clips if k.track == "V1")
    y = view.rows.y["V1"] + 10
    x0 = int(view.vp.x_of(c.start + c.duration / 2))
    # on cherche un trou : on déplace le plan tout à droite de la piste
    dx = int((last + 200 - c.start) * view.vp.zoom)
    view.vp.width = 100000
    view.resize(100000, view.height())
    before = c.start
    QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y))
    QTest.mouseMove(view, QPoint(x0 + dx // 2, y))
    QTest.mouseMove(view, QPoint(x0 + dx, y))
    QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(x0 + dx, y))
    assert tl.clip(c.id).start != before
    s.undo()
    assert tl.clip(c.id).start == before               # une seule annulation suffit


def test_drag_onto_another_clip_is_refused_and_nothing_changes(view):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    tl = view.tl
    a, b = [k for k in tl.clips if k.track == "V1"][:2]
    msgs, snapshot = [], tl.to_dict()
    view.rejected.connect(msgs.append)
    y = view.rows.y["V1"] + 10
    x0 = int(view.vp.x_of(a.start + a.duration / 2))
    x1 = int(view.vp.x_of(b.start + b.duration / 2)) + 3          # on lâche au milieu de b : chevauchement
    QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y))
    QTest.mouseMove(view, QPoint((x0 + x1) // 2, y))
    QTest.mouseMove(view, QPoint(x1, y))
    QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(x1, y))
    assert msgs and tl.to_dict() == snapshot


def test_click_on_ruler_moves_the_playhead_and_wheel_zoom_is_anchored(view):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    heads = []
    view.playheadChanged.connect(heads.append)
    QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(200, 5))
    assert heads == [round(view.vp.frame_of(200))]
    f = view.vp.frame_of(500)
    view.vp.zoom_at(500, 1.25)
    assert view.vp.frame_of(500) == pytest.approx(f)


def test_locked_clip_is_not_draggable(view):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    c = next(k for k in view.tl.clips if k.track == "V2" and k.start > 0)
    c.locked = True
    view.refresh_model()
    snapshot = view.tl.to_dict()
    y = view.rows.y["V2"] + 10
    x0 = int(view.vp.x_of(c.start + c.duration / 2))
    QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y))
    QTest.mouseMove(view, QPoint(x0 + 80, y))
    QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(x0 + 80, y))
    assert view.tl.to_dict() == snapshot


def _trim_setup(view):
    tl = view.tl
    c = next(k for k in tl.clips if k.track == "V1" and k.start > 0)
    nxt = min((k.start for k in tl.clips if k.track == "V1" and k.start >= c.end), default=None)
    return tl, c, nxt, view.rows.y["V1"] + 10


def test_trim_tail_drag_previews_clamps_and_is_one_undo_step(view):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    tl, c, nxt, y = _trim_setup(view)
    xr = int(view.vp.x_of(c.end)) - 2
    gap, dur0 = nxt - c.end, c.duration
    QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(xr, y))
    QTest.mouseMove(view, QPoint(xr + 3, y))
    QTest.mouseMove(view, QPoint(xr + int((gap + 40) * view.vp.zoom), y))        # on tire BIEN au-delà du voisin
    assert view._trim["delta"] == gap and view._trim["clamped"]                 # la prévisualisation s'arrête à la limite
    assert tl.clip(c.id).duration == dur0                                        # rien d'écrit pendant le glisser
    QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(xr + int((gap + 40) * view.vp.zoom), y))
    assert tl.clip(c.id).duration == dur0 + gap
    view.session.undo()
    assert tl.clip(c.id).duration == dur0


def test_trim_head_updates_source_in_and_escape_cancels(view):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    tl, c, nxt, y = _trim_setup(view)
    xl = int(view.vp.x_of(c.start)) + 2
    src0, start0, end0, snap = c.src_in, c.start, c.end, tl.to_dict()
    QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(xl, y))
    QTest.mouseMove(view, QPoint(xl + 3, y))
    QTest.mouseMove(view, QPoint(xl + int(10 * view.vp.zoom) + 3, y))
    assert view._trim["delta"] > 0
    QTest.keyClick(view, Qt.Key_Escape)
    QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(xl + 40, y))
    assert tl.to_dict() == snap                                                  # Échap : rien n'a été écrit
    QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(xl, y))
    QTest.mouseMove(view, QPoint(xl + 3, y))
    QTest.mouseMove(view, QPoint(xl + int(10 * view.vp.zoom) + 3, y))
    QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(xl + int(10 * view.vp.zoom) + 3, y))
    n = tl.clip(c.id)
    assert n.start > start0 and n.src_in == pytest.approx(src0 + (n.start - start0) / FPS)
    assert n.end == end0                                                        # la fin ne bouge pas


def test_trim_respects_source_length(view):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    tl, c, nxt, y = _trim_setup(view)
    view.source_len = lambda media: c.src_in + c.duration / FPS + 0.2           # 0,2 s = 5 images de poignée après le plan
    xr = int(view.vp.x_of(c.end)) - 2
    QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(xr, y))
    QTest.mouseMove(view, QPoint(xr + 3, y))
    QTest.mouseMove(view, QPoint(xr + int(30 * view.vp.zoom), y))
    assert view._trim["hi"] == 5 and view._trim["delta"] <= 5
    QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(xr + int(30 * view.vp.zoom), y))
