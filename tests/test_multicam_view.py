"""Vue Qt du lecteur multicam, avec de vraies (petites) vidéos : images reçues par tous les angles, touche = bascule, perf du dessin."""

import os
import shutil
import subprocess

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg absent")

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from kolnoa_editor.player.controller import MulticamPlayer
from kolnoa_editor.player.scheduler import MediaInfo
from kolnoa_editor.ui.multicam_view import MulticamView
from tests.test_multicam_live import tl_cam


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("v")
    out = []
    for i in range(3):
        f = d / f"c{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration=8",
                        "-c:v", "libx264", "-preset", "ultrafast", "-g", "12", "-pix_fmt", "yuv420p", str(f)], check=True)
        out.append(str(f))
    return out


def test_view_plays_three_angles_switches_with_keys_and_stays_cheap(clips):
    app = QApplication.instance() or QApplication([])
    ids = ["A7", "OSMO", "DRONE"]
    media = {a: MediaInfo(c, 640, 360, 25, None, 360, 8.0) for a, c in zip(ids, clips)}
    tl = tl_cam()
    for a in ids:                       # même décalage pour les trois : t_angle = 0,5 s à t = 12,5 s (clips de 8 s)
        tl.group("g001").angle(a).offset = 12.0
    v = MulticamView(lambda mk: MulticamPlayer(tl, "g001", "V1", media, mk, budget=1e10))
    v.resize(960, 540)
    v.show()
    v.player.seek(12.5)
    wait(500)
    v.player.play()
    wait(1500)
    assert set(v.images) == set(ids)                       # chaque angle a reçu des images (tous lus en parallèle)
    assert v.player.shown == "A7"
    QTest.keyClick(v, Qt.Key_2)
    wait(400)
    assert v.player.shown == "OSMO" and v.player.stats.forced_swaps == 0
    assert v.player.stats.switch_latencies[-1] < 0.5
    err = {a: abs(v.player.backends[a].position() - v.player.t_angle(a, v.player.clock.position())) for a in ids}
    assert max(err.values()) < 0.35, err
    refused = []
    v.refused.connect(refused.append)
    QTest.keyClick(v, Qt.Key_9)                            # pas d'angle sur la touche 9 → refus signalé, rien ne casse
    assert refused
    paints = sorted(v.paint_ms)[5:] or v.paint_ms
    assert paints[len(paints) // 2] < 40                   # dessin raisonnable (bac à sable, rendu logiciel, images 360p)
    v.close()
