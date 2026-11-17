"""Formes d'onde asynchrones : jamais bloquant, progressif, priorisé, cache disque, annulation, fluidité de l'interface."""

import os
import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from kolnoa_editor.timeline import Clip, Timeline, Track
from kolnoa_editor.timeline.session import Session
from kolnoa_editor.ui.peak_service import PeakService
from kolnoa_editor.ui.timeline_view import TimelineView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def pump(ms=50):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(cond, timeout=8.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        pump(20)
        if cond():
            return True
    return False


def fake_compute(chunks=5, delay=0.12, level=100, order=None, gate=None):
    """Calcul factice : `chunks` tranches de 100 crêtes (2 s) ; `gate` (Event) bloque le début ; `order` note l'ordre de démarrage."""
    def run(path, on_bins, cancel, procs):
        if order is not None:
            order.append(path)
        if gate is not None:
            gate.wait(5)
        for _ in range(chunks):
            if cancel.is_set():
                return False
            time.sleep(delay)
            on_bins(np.full(100, level, dtype=np.uint8))
        return True
    return run


def test_get_never_blocks_and_waveform_fills_progressively(qapp):
    svc = PeakService(compute=fake_compute(chunks=6, delay=0.2), workers=1)
    seen = []
    svc.changed.connect(lambda m: seen.append(svc._pyr[m].duration if m in svc._pyr else 0))
    t0 = time.perf_counter()
    assert svc.get("a.wav") is None                                   # rien de prêt : réponse immédiate, pas d'attente
    assert time.perf_counter() - t0 < 0.05
    assert wait_for(lambda: svc.get("a.wav") is not None, 5)
    part = svc.get("a.wav")
    assert part is not None and not part.complete and part.covers(0.5) and not part.covers(10.0)   # image partielle utilisable
    assert wait_for(lambda: svc.get("a.wav") is not None and svc.get("a.wav").complete, 8)
    assert svc.get("a.wav").duration == pytest.approx(6 * 100 * 0.02)
    assert len(set(seen)) >= 3 and seen == sorted(seen)               # plusieurs étapes croissantes, jamais de recul
    assert svc.progress("a.wav") == 1.0
    svc.close()


def test_visible_media_are_computed_first(qapp):
    gate, order = threading.Event(), []
    svc = PeakService(compute=fake_compute(chunks=1, delay=0.01, gate=gate, order=order), workers=1)
    svc.get("bloque.wav")                                             # occupe l'unique fil
    wait_for(lambda: order == ["bloque.wav"], 3)
    for m in ("a.wav", "b.wav", "c.wav", "d.wav"):
        svc.get(m)
    svc.want(["d.wav", "c.wav"])                                      # d et c sont à l'écran
    gate.set()
    assert wait_for(svc.idle, 5)
    assert order[1:3] == ["d.wav", "c.wav"]
    svc.close()


def test_disk_cache_roundtrip_corruption_and_atomicity(qapp, tmp_path):
    media = tmp_path / "r.wav"
    media.write_bytes(b"x" * 100)
    calls = []
    comp = fake_compute(chunks=2, delay=0.01)
    svc = PeakService(cache_dir=tmp_path / "c", compute=lambda *a: (calls.append(1), comp(*a))[1], workers=1)
    svc.get(str(media))
    assert wait_for(lambda: svc.idle() and svc.get(str(media)) is not None and svc.get(str(media)).complete, 5)
    assert len(list((tmp_path / "c").glob("*.npy"))) == 1 and not list((tmp_path / "c").glob("*.tmp"))
    svc.close()
    svc2 = PeakService(cache_dir=tmp_path / "c", compute=lambda *a: (calls.append(1), comp(*a))[1], workers=1)
    svc2.get(str(media))
    assert wait_for(lambda: svc2.get(str(media)) is not None, 5)
    assert svc2.stats["from_cache"] == 1 and len(calls) == 1          # relancé : lu depuis le disque, pas recalculé
    svc2.close()
    f = next((tmp_path / "c").glob("*.npy"))
    f.write_bytes(b"corrompu")                                        # cache abîmé → recalcul, pas d'exception
    svc3 = PeakService(cache_dir=tmp_path / "c", compute=lambda *a: (calls.append(1), comp(*a))[1], workers=1)
    svc3.get(str(media))
    assert wait_for(lambda: svc3.get(str(media)) is not None and svc3.get(str(media)).complete, 5)
    assert len(calls) == 2
    svc3.close()
    media.write_bytes(b"y" * 200)                                     # le rush a changé → nouvelle clé de cache
    svc4 = PeakService(cache_dir=tmp_path / "c", compute=lambda *a: (calls.append(1), comp(*a))[1], workers=1)
    svc4.get(str(media))
    assert wait_for(lambda: svc4.idle() and svc4.get(str(media)) is not None, 5)
    assert len(calls) == 3
    svc4.close()


def test_media_without_audio_is_remembered_and_not_retried(qapp):
    n = []
    svc = PeakService(compute=lambda p, ob, c, pr: (n.append(1), False)[1], workers=1)
    svc.get("muet.mp4")
    assert wait_for(svc.idle, 3)
    for _ in range(5):
        assert svc.get("muet.mp4") is None
    pump(100)
    assert len(n) == 1
    svc.close()


def test_close_cancels_running_work_quickly(qapp):
    svc = PeakService(compute=fake_compute(chunks=500, delay=0.05), workers=2)
    svc.get("long1.wav"); svc.get("long2.wav")
    pump(200)
    t0 = time.perf_counter()
    svc.close()
    assert time.perf_counter() - t0 < 2.0
    assert svc.stats["computed"] == 0


def make_big_project(tmp_path, n_tracks=8, per=60):
    tl = Timeline(title="T", fps=25, tracks=[Track(f"A{i}", "audio") for i in range(n_tracks)])
    k = 0
    for ti in range(n_tracks):
        pos = 0
        for i in range(per):
            k += 1
            tl.clips.append(Clip(f"c{k:04d}", f"A{ti}", f"rush{(k % 40):02d}.wav", pos, 120, src_in=float(i)))
            pos += 130
    tl.validate()
    return Session.create(tmp_path / "s", tl)


def test_view_stays_fluid_while_waves_compute_and_fills_in(qapp, tmp_path):
    svc = PeakService(compute=fake_compute(chunks=4, delay=0.05), workers=2)
    s = make_big_project(tmp_path)
    v = TimelineView(s, peak_service=svc)
    v.resize(1400, v.rows.total_h + 4)
    v.vp.zoom, v.vp.offset = 0.5, 0.0
    t_paint = []
    t_end = time.time() + 1.5
    i = 0
    while time.time() < t_end:                                         # pendant que les calculs tournent : pan + zoom + dessin
        v.vp.pan_px(30)
        if i % 5 == 0:
            v.vp.zoom_at(500, 1.05 if (i // 5) % 2 else 0.95)
        t0 = time.perf_counter()
        v.grab()
        t_paint.append((time.perf_counter() - t0) * 1000)
        pump(5)
        i += 1
    t_paint.sort()
    med, p95 = t_paint[len(t_paint) // 2], t_paint[int(len(t_paint) * .95)]
    print(f"[perf] dessin pendant le calcul des ondes : médiane {med:.1f} ms, p95 {p95:.1f} ms sur {len(t_paint)} images")
    assert med < 60 and p95 < 150
    assert svc._pyr, "des formes d'onde ont dû arriver pendant le défilement"
    assert wait_for(svc.idle, 20)
    v.vp.offset, v.vp.zoom = 0.0, 0.5
    pump(150)
    v.grab()
    assert all(p.complete for p in svc._pyr.values())
    svc.close()


def test_wave_images_are_only_cached_when_final(qapp, tmp_path):
    gate = threading.Event()
    svc = PeakService(compute=fake_compute(chunks=3, delay=0.05, gate=gate), workers=1)
    tl = Timeline(title="T", fps=25, tracks=[Track("A1", "audio")])
    tl.clips.append(Clip("c1", "A1", "w.wav", 0, 150, src_in=0.0))         # 6 s ; les crêtes finales couvrent 6 s (300 crêtes)
    s = Session.create(tmp_path / "s", tl)
    v = TimelineView(s, peak_service=svc)
    v.resize(900, v.rows.total_h + 4)
    v.vp.zoom, v.vp.offset = 2.0, 0.0
    v.grab()                                                              # demande le calcul (bloqué)
    assert not v._wave_cache
    gate.set()
    assert wait_for(lambda: svc.get("w.wav") is not None and not svc.get("w.wav").complete or svc.idle(), 5)
    wait_for(svc.idle, 5)
    pump(200)
    v.grab()
    assert any(k[0] == "w.wav" for k in v._wave_cache)                    # image définitive mise en cache
    svc.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg absent")
def test_real_ffmpeg_stream_matches_the_synchronous_loader(qapp, tmp_path):
    from kolnoa_editor.ui.peaks import load_peaks
    f = tmp_path / "t.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=330:duration=25", str(f)], check=True)
    svc = PeakService(cache_dir=tmp_path / "c", workers=1)
    svc.get(str(f))
    assert wait_for(lambda: svc.get(str(f)) is not None and svc.get(str(f)).complete, 15)
    ref = load_peaks(str(f))
    got = svc.get(str(f))
    assert abs(len(got.levels[0]) - len(ref.levels[0])) <= 1
    n = min(len(got.levels[0]), len(ref.levels[0]))
    assert np.array_equal(got.levels[0][:n - 1], ref.levels[0][:n - 1])
    svc.close()
