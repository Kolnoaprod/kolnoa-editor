"""V0.1 : lecture (tête de lecture qui avance) et aperçu image réel d'un montage non multicam, sur de vrais fichiers ffmpeg."""

import os
import subprocess
from dataclasses import asdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from kolnoa_editor.app import ProjectController
from kolnoa_editor.ffmpeg import ffmpeg_bin
from kolnoa_editor.timeline.model import Clip
from kolnoa_editor.timeline.ops import AddClip
from kolnoa_editor.ui.app_window import AppWindow
from kolnoa_editor.ui.preview import frame_request, grab_frame


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _mk(path, color, rate=25):
    subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r={rate}:d=4", "-f", "lavfi", "-i",
                    "sine=f=440:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


@pytest.fixture
def win(app, tmp_path):
    root = tmp_path / "m"
    root.mkdir()
    _mk(root / "rouge.mp4", "red")
    _mk(root / "bleu.mp4", "blue")
    c = ProjectController.open(root, tmp_path / "d")
    assert c.import_rushes() == 2
    fps = c.tl.fps
    c.session.do(AddClip(asdict(Clip("c1", "V1", "rouge.mp4", 0, 3 * fps, 0.0))), "pose")
    c.session.do(AddClip(asdict(Clip("c2", "V1", "bleu.mp4", 3 * fps, 3 * fps, 0.0))), "pose")
    w = AppWindow(c, player_factory=lambda ctl, g: None)
    w.refresh()
    yield w
    w.close()


def test_frame_request_maps_timeline_to_source(win):
    tl = win.ctl.tl
    fps = tl.fps
    assert frame_request(tl, int(1.0 * fps)) == ("rouge.mp4", pytest.approx(1.0, abs=1e-3))
    m, t = frame_request(tl, 4 * fps)
    assert m == "bleu.mp4" and t == pytest.approx(1.0, abs=1e-3)
    assert frame_request(tl, 10 * fps) is None


def test_preview_shows_the_visible_clip(win):
    fps = win.ctl.tl.fps
    for frame, want in ((fps, "r"), (4 * fps, "b")):
        win.tlv.set_playhead(frame)
        win._update_preview()
        img = win.preview.pixmap().toImage()
        px = img.pixelColor(img.width() // 2, img.height() // 2)
        assert (px.red() > 150 and px.blue() < 100) if want == "r" else (px.blue() > 150 and px.red() < 100)
    assert grab_frame(win.ctl.project.root, "absent.mp4", 0) is None


def test_play_prepares_continuous_video_and_audio(win):
    import time
    from kolnoa_editor.probe import probe
    from kolnoa_editor.timeline.audio_mix import measure_loudness

    win.toggle_play()
    deadline = time.monotonic() + 20
    while win._operation.running and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(.02)
    assert win._operation.bar.value() == 100, win.status.text()
    path = win._program_player.source().toLocalFile()
    media = probe(path)
    assert media.has_audio and media.width == 960
    assert media.duration == pytest.approx(6, abs=.15)
    assert measure_loudness(path).integrated_lufs is not None
    assert win._program_player.audioOutput() is not None
    win.pause()
