"""Essais de rendu MP4 réels pour les vitesses, sans rush client.

Les médias synthétiques sont à 50 i/s ; la timeline de sortie est à 25 i/s.
Ce test vérifie le conteneur final, le son et l'invalidation du cache.
"""
import shutil
import subprocess

import pytest

from kolnoa_editor.probe import probe
from kolnoa_editor.render_timeline import render_timeline
from kolnoa_editor.timeline.model import Clip, Timeline, Track

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe indisponibles",
)


def _source(path):
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=50:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )


def _timeline(speed):
    tl = Timeline(
        title="speed real render", fps=25, resolution=(320, 180),
        tracks=[Track("V1", "video")],
        clips=[Clip(
            "v1", "V1", "source.mp4", 0, 50, 0.0,
            audio="source", extra={"speed": speed},
        )],
    )
    tl.validate()
    return tl


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0])
def test_real_mp4_50fps_to_25fps_with_audio_and_cache(tmp_path, speed):
    source = tmp_path / "source.mp4"
    _source(source)
    tl = _timeline(speed)
    cache = tmp_path / "cache"
    out = tmp_path / f"speed_{speed}.mp4"
    report = render_timeline(tl, tmp_path, out, cache)
    info = probe(out)

    assert out.is_file() and out.stat().st_size > 0
    assert report.frames == 50 and report.encoded == 1
    assert abs(info.duration - 2.0) < 0.15
    assert abs((info.fps or 0) - 25) < 0.5
    assert info.has_audio

    repeat = render_timeline(tl, tmp_path, tmp_path / "again.mp4", cache)
    assert repeat.encoded == 0 and repeat.reused == 1


def test_speed_change_invalidates_segment_cache(tmp_path):
    _source(tmp_path / "source.mp4")
    cache = tmp_path / "cache"
    first = render_timeline(_timeline(0.5), tmp_path, tmp_path / "slow.mp4", cache)
    second = render_timeline(_timeline(2.0), tmp_path, tmp_path / "fast.mp4", cache)
    assert first.encoded == 1
    assert second.encoded == 1 and second.reused == 0
