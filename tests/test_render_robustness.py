"""Rendu : cache, reprise, annulation, atomicité, sources hétérogènes (4K50, mono 44,1 kHz, VFR, drone muet)."""

import threading
from pathlib import Path

import pytest
from helpers import ff

from kolnoa_editor import renderer
from kolnoa_editor.decision_list import DecisionList, Music, Segment
from kolnoa_editor.ffmpeg import CancelToken, Cancelled, FFmpegError, ffmpeg_bin, run
from kolnoa_editor.preflight import PreflightError
from kolnoa_editor.probe import probe
from kolnoa_editor.project import Project
from kolnoa_editor.renderer import clean_cache, render

SONY = "MAIRIE/A7IV/20261011_C4837.MP4"
DRONE = "SOIREE/DRONE/DJI_20261108192622_0015_D.MP4"
CANON = "SOIREE/CANON/A_0009C238A261108_113335HL_CANON.MP4"


def three(**kw):
    return DecisionList(
        title="T", resolution=(640, 360),
        segments=[Segment("s001", SONY, 0, 1.0), Segment("s002", DRONE, 0, 1.0, audio="mute"),
                  Segment("s003", CANON, 0, 1.0)], **kw)


@pytest.fixture
def encodes(monkeypatch):
    calls = []
    original = renderer._encode_segment

    def spy(project, dl, seg, kind, src, out, cancel):
        result = original(project, dl, seg, kind, src, out, cancel)
        calls.append(seg.id)  # seulement les encodages menés à terme
        return result

    monkeypatch.setattr(renderer, "_encode_segment", spy)
    return calls


def test_cache_reuses_unchanged_segments(mariage, encodes):
    project = Project(mariage)
    dl = three()
    render(project, dl, "final")
    assert encodes == ["s001", "s002", "s003"]
    encodes.clear()
    render(project, dl, "final")
    assert encodes == []  # tout vient du cache
    dl.segments[1].end = 1.5  # un seul plan retouché
    render(project, dl, "final")
    assert encodes == ["s002"]


def test_cancel_keeps_previous_render_and_resume_continues(mariage, encodes):
    project = Project(mariage)
    out = project.renders_dir / "x.mp4"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"ANCIEN RENDU")
    token = CancelToken()

    def progress(i, n, label):
        if i == 3:
            token.cancel()

    with pytest.raises(Cancelled):
        render(project, three(), "final", out=out, on_progress=progress, cancel=token)
    assert out.read_bytes() == b"ANCIEN RENDU"                  # rendu précédent intact
    assert not list(out.parent.glob("*.part.*"))              # aucun fichier tronqué
    assert encodes == ["s001", "s002"]
    encodes.clear()
    render(project, three(), "final", out=out)
    assert encodes == ["s003"]                                  # reprise : seul le segment manquant
    assert probe(out).duration == pytest.approx(3.0, abs=0.3)


def test_failed_music_pass_publishes_nothing(mariage, monkeypatch):
    project = Project(mariage)
    real_run = renderer.run

    def failing(cmd, cancel=None):
        if "-filter_complex" in cmd:
            raise FFmpegError("échec simulé")
        return real_run(cmd, cancel)

    monkeypatch.setattr(renderer, "run", failing)
    dl = three(music=Music("musique.mp3"))
    with pytest.raises(FFmpegError):
        render(project, dl, "final")
    assert not project.render_path(1, "final").exists()
    assert not list(project.renders_dir.glob("*.part.*"))
    assert not list(project.tmp_dir.glob("*"))


def test_preflight_error_blocks_render_before_any_encoding(mariage, encodes):
    dl = three()
    dl.segments[0].clip = "MAIRIE/A7IV/absent.MP4"
    with pytest.raises(PreflightError, match="SRC_MISSING"):
        render(Project(mariage), dl, "final")
    assert encodes == []


def test_clean_cache_only_touches_cache(mariage):
    project = Project(mariage)
    render(project, three(), "final")
    out = project.render_path(1, "final")
    assert any((project.cache_dir / "segments").iterdir())
    clean_cache(project)
    assert not any((project.cache_dir / "segments").iterdir())
    assert out.is_file() and (mariage / SONY).is_file()


def test_mixed_4k50_mono44k_vfr_and_muted_drone(mariage):
    uhd = mariage / "SOIREE" / "A7IV_4K50.MP4"
    ff("-f", "lavfi", "-i", "testsrc=s=3840x2160:r=50:d=1.5", "-c:v", "libx264", "-preset", "ultrafast",
       "-pix_fmt", "yuv420p", "-an", str(uhd))
    mono = mariage / "SOIREE" / "mono44.MP4"
    ff("-f", "lavfi", "-i", "testsrc=s=640x360:r=25:d=2", "-f", "lavfi", "-i", "sine=f=500:r=44100:d=2",
       "-ac", "1", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(mono))
    vfr = mariage / "SOIREE" / "vfr.MP4"
    ff("-f", "lavfi", "-i", "testsrc=s=640x360:r=25:d=4", "-vf", "select='lt(mod(n,5),3)'",
       "-fps_mode", "vfr", "-c:v", "libx264", "-an", str(vfr))
    assert probe(vfr).is_vfr and not probe(uhd).is_vfr

    dl = DecisionList(
        title="Mix", resolution=(1280, 720), fps=25,
        segments=[Segment("s001", "SOIREE/A7IV_4K50.MP4", 0, 1.0),
                  Segment("s002", "SOIREE/mono44.MP4", 0, 1.0),
                  Segment("s003", DRONE, 0, 1.0, audio="mute"),
                  Segment("s004", "SOIREE/vfr.MP4", 0.5, 2.0)])
    out = render(Project(mariage), dl, "final")
    info = probe(out)
    assert info.duration == pytest.approx(4.5, abs=0.35)  # 1 + 1 + 1 + 1,5 s
    assert (info.width, info.height) == (1280, 720)
    assert info.fps == pytest.approx(25, abs=0.01) and not info.is_vfr
    assert info.has_audio and info.sample_rate == 48000 and info.audio_channels == 2


def test_run_can_be_cancelled_promptly():
    token = CancelToken()
    threading.Timer(0.5, token.cancel).start()
    import time

    t0 = time.time()
    with pytest.raises(Cancelled):
        run([ffmpeg_bin(), "-v", "error", "-f", "lavfi", "-i", "testsrc=s=1920x1080:r=25:d=3000",
             "-f", "null", "-"], token)
    assert time.time() - t0 < 10


def _frame_times(path):
    import subprocess

    from kolnoa_editor.ffmpeg import ffprobe_bin

    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "frame=pts_time", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.split()
    return [float(x.strip(",")) for x in out]


def test_joins_are_frame_exact_with_constant_frame_rate(mariage):
    """Régression : l'AAC par segment laissait un trou d'une image à chaque raccord (cadence 24,3)."""
    dl = three()
    out = render(Project(mariage), dl, "final")
    times = _frame_times(out)
    assert len(times) == 75                                   # 3 x 1 s x 25 i/s, exactement
    assert max(abs((b - a) - 0.04) for a, b in zip(times, times[1:])) < 0.002   # aucun trou
    info = probe(out)
    assert info.duration == pytest.approx(3.0, abs=0.03) and not info.is_vfr


def test_vfr_source_still_yields_exact_frame_count(mariage):
    """Régression : une source à cadence variable faisait perdre des images au début du plan."""
    vfr = mariage / "SOIREE" / "vfr.MP4"
    ff("-f", "lavfi", "-i", "testsrc=s=640x360:r=25:d=4", "-vf", "select='lt(mod(n,5),3)'",
       "-fps_mode", "vfr", "-c:v", "libx264", "-an", str(vfr))
    dl = DecisionList(title="v", resolution=(640, 360), segments=[Segment("s001", "SOIREE/vfr.MP4", 0.5, 2.0)])
    times = _frame_times(render(Project(mariage), dl, "final"))
    assert len(times) == 38 and times[0] == pytest.approx(0.0, abs=0.001)
