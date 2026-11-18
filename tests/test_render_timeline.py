"""Export MP4 d'une timeline avec de VRAIS médias générés par ffmpeg : pistes V1+V2, trou noir, audio multi-pistes, cache, refus."""

import shutil
import subprocess

import pytest

from kolnoa_editor.probe import probe
from kolnoa_editor.render_timeline import TimelineRenderError, render_timeline
from kolnoa_editor.timeline import Clip, Timeline
from kolnoa_editor.timeline.model import Track

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg absent")


def make_media(path, color, freq, dur=6):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:r=25:d={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def frame_color(path, t):
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-vf", "scale=1:1,format=rgb24",
                        "-f", "rawvideo", "-"], capture_output=True, check=True)
    return tuple(r.stdout[:3])


@pytest.fixture
def media(tmp_path):
    make_media(tmp_path / "red.mp4", "red", 440)
    make_media(tmp_path / "blue.mp4", "blue", 880)
    return tmp_path


def tl_of(clips, tracks=None):
    tl = Timeline(title="t", fps=25, resolution=(320, 180),
                  tracks=tracks or [Track("V1", "video"), Track("V2", "video"), Track("A1", "audio"), Track("A2", "audio")],
                  clips=clips)
    tl.validate()
    return tl


def test_export_layers_gap_and_audio(media, tmp_path):
    tl = tl_of([
        Clip("v1", "V1", "red.mp4", 0, 75, 0.0, audio="mute"),          # 0–3 s rouge
        Clip("v2", "V2", "blue.mp4", 25, 25, 0.0, audio="mute"),         # 1–2 s bleu PAR-DESSUS
        Clip("v3", "V1", "red.mp4", 100, 25, 1.0, audio="mute"),         # 4–5 s rouge après un trou noir 3–4 s
        Clip("a1", "A1", "red.mp4", 0, 125, 0.0),
        Clip("a2", "A2", "blue.mp4", 50, 50, 0.0, gain_db=-6.0),
    ])
    out = tmp_path / "out" / "film.mp4"
    rep = render_timeline(tl, media, out, tmp_path / "cache")
    info = probe(out)
    assert abs(info.duration - 5.0) < 0.15 and info.has_audio
    assert rep.frames == 125 and rep.encoded == rep.segments and any("noir" in w.lower() or "trou" in w.lower() for w in rep.warnings)
    r, b, black = frame_color(out, 0.5), frame_color(out, 1.5), frame_color(out, 3.5)
    assert r[0] > 150 and r[2] < 100                    # rouge
    assert b[2] > 150 and b[0] < 100                    # bleu au-dessus
    assert sum(black) < 60                              # noir explicite
    again = render_timeline(tl, media, tmp_path / "out" / "film2.mp4", tmp_path / "cache")
    assert again.encoded == 0 and again.reused == again.segments       # cache : rien de ré-encodé


def test_edit_reencodes_only_changed_segment(media, tmp_path):
    mk = lambda n: tl_of([Clip("v1", "V1", "red.mp4", 0, 50, 0.0, audio="mute"), Clip("v2", "V1", "blue.mp4", 50, n, 0.0, audio="mute"),
                          Clip("a1", "A1", "red.mp4", 0, 50 + n)])
    render_timeline(mk(50), media, tmp_path / "a.mp4", tmp_path / "c")
    rep = render_timeline(mk(75), media, tmp_path / "b.mp4", tmp_path / "c")
    assert rep.encoded == 1 and rep.reused == 1


def test_refuses_missing_media_and_empty_and_overrun(media, tmp_path):
    with pytest.raises(TimelineRenderError):
        render_timeline(tl_of([Clip("v1", "V1", "absent.mp4", 0, 25, 0.0)]), media, tmp_path / "x.mp4", tmp_path / "c")
    with pytest.raises(TimelineRenderError):
        render_timeline(tl_of([]), media, tmp_path / "x.mp4", tmp_path / "c")
    with pytest.raises(TimelineRenderError):
        render_timeline(tl_of([Clip("v1", "V1", "red.mp4", 0, 25 * 20, 0.0)]), media, tmp_path / "x.mp4", tmp_path / "c")
    assert not (tmp_path / "x.mp4").exists()


def test_output_resolution_choice_with_mixed_source_sizes(tmp_path):
    """Rushes de tailles différentes (un « 4K » et un « 1080p » à l'échelle du test, tous deux à 50 i/s) → même timeline, sortie au choix."""
    def gen(path, color, size):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=50:d=3", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(path)], check=True)
    gen(tmp_path / "big.mp4", "red", "1280x720")
    gen(tmp_path / "small.mp4", "blue", "640x360")
    tl = Timeline(title="t", fps=50, resolution=(1280, 720), tracks=[Track("V1", "video"), Track("A1", "audio")],
                  clips=[Clip("a", "V1", "big.mp4", 0, 50, 0.0, audio="mute"), Clip("b", "V1", "small.mp4", 50, 50, 0.0, audio="mute"),
                         Clip("s", "A1", "big.mp4", 0, 100, 0.0)])
    tl.validate()
    for name, want in (("1080p", (1920, 1080)), ((640, 360), (640, 360)), (None, (1280, 720))):
        out = tmp_path / f"o_{name}.mp4"
        rep = render_timeline(tl, tmp_path, out, tmp_path / "cache", resolution=name)
        i = probe(out)
        assert (i.width, i.height) == want and rep.resolution == want
        assert abs(i.duration - 2.0) < 0.15 and abs((i.fps or 0) - 50) < 0.5          # 50 i/s conservés
    with pytest.raises(ValueError):
        render_timeline(tl, tmp_path, tmp_path / "x.mp4", tmp_path / "cache", resolution="8k")
