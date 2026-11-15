"""Validation pré-rendu."""

from collections import namedtuple

import pytest
from helpers import ff

from kolnoa_editor import preflight as pf
from kolnoa_editor.decision_list import DecisionList, Music, Segment
from kolnoa_editor.ffmpeg import FFmpegError
from kolnoa_editor.probe import probe
from kolnoa_editor.project import Project
from kolnoa_editor.proxies import build_proxies

SONY = "MAIRIE/A7IV/20261011_C4837.MP4"
DRONE = "SOIREE/DRONE/DJI_20261108192622_0015_D.MP4"


def dl_of(*segments, **kw):
    return DecisionList(title="P", resolution=(1280, 720), segments=list(segments), **kw)


def codes(issues, level=None):
    return {i.code for i in issues if level is None or i.level == level}


def test_clean_list_has_no_errors(mariage):
    issues = pf.preflight(Project(mariage), dl_of(Segment("s001", SONY, 0, 2.0)), "final")
    assert not pf.has_errors(issues)


def test_missing_and_unreadable_and_outside_sources(mariage):
    (mariage / "vide.MP4").write_bytes(b"")
    (mariage / "faux.MP4").write_bytes(b"ceci n'est pas une video")
    issues = pf.preflight(Project(mariage), dl_of(
        Segment("s001", "absent.MP4", 0, 1), Segment("s002", "vide.MP4", 0, 1),
        Segment("s003", "faux.MP4", 0, 1), Segment("s004", "../hors.MP4", 0, 1)), "final")
    assert codes(issues, "error") >= {"SRC_MISSING", "SRC_EMPTY", "SRC_UNREADABLE", "PATH_OUTSIDE"}


def test_range_beyond_clip_duration(mariage):
    issues = pf.preflight(Project(mariage), dl_of(Segment("s001", SONY, 4.0, 9.0)), "final")
    assert "RANGE_BEYOND" in codes(issues, "error")


def test_empty_list_and_invalid_structure(mariage):
    assert "EMPTY" in codes(pf.preflight(Project(mariage), dl_of()))
    bad = dl_of(Segment("s001", SONY, 0, 1), Segment("s001", SONY, 1, 2))
    assert "INVALID_LIST" in codes(pf.preflight(Project(mariage), bad))


def test_ffmpeg_missing_is_reported(mariage, monkeypatch):
    def boom():
        raise FFmpegError("ffmpeg introuvable")

    monkeypatch.setattr(pf, "ffmpeg_bin", boom)
    issues = pf.preflight(Project(mariage), dl_of(Segment("s001", SONY, 0, 1)))
    assert codes(issues) == {"FFMPEG_MISSING"}


def test_nvenc_absent_warning_for_final_only(mariage, monkeypatch):
    monkeypatch.setattr(pf, "has_nvenc", lambda: False)
    dl = dl_of(Segment("s001", SONY, 0, 1))
    assert "NVENC_ABSENT" in codes(pf.preflight(Project(mariage), dl, "final"), "warning")
    assert "NVENC_ABSENT" not in codes(pf.preflight(Project(mariage), dl, "proxy"))


def test_music_problems(mariage):
    project = Project(mariage)
    seg = Segment("s001", SONY, 0, 5.0)
    assert "MUSIC_MISSING" in codes(pf.preflight(project, dl_of(seg, music=Music("nope.mp3"))), "error")
    assert "MUSIC_NO_AUDIO" in codes(pf.preflight(project, dl_of(seg, music=Music(DRONE))), "error")
    short = dl_of(seg, music=Music("musique.mp3", start=28.0))  # musique de 30 s
    assert "MUSIC_SHORT" in codes(pf.preflight(project, short), "warning")


def test_disk_space(mariage, monkeypatch):
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(pf.shutil, "disk_usage", lambda p: Usage(100, 100, 1))
    issues = pf.preflight(Project(mariage), dl_of(Segment("s001", SONY, 0, 3)), "final")
    assert "DISK_SPACE" in codes(issues, "error")


def test_drone_without_sound_is_explained_not_blocked(mariage):
    issues = pf.preflight(Project(mariage), dl_of(Segment("s001", DRONE, 0, 2)), "final")
    assert not pf.has_errors(issues)
    assert "NO_AUDIO" in codes(issues, "info")
    assert "FPS_CONVERT" in codes(issues, "warning")  # 30 i/s -> 25 i/s


def test_fps_decimation_is_informational(mariage):
    src = mariage / "SOIREE" / "clip50.MP4"
    ff("-f", "lavfi", "-i", "testsrc=s=640x360:r=50:d=1", "-c:v", "libx264", "-an", str(src))
    issues = pf.preflight(Project(mariage), dl_of(Segment("s001", "SOIREE/clip50.MP4", 0, 1)))
    assert "FPS_DECIMATION" in codes(issues, "info") and "FPS_CONVERT" not in codes(issues)


def test_vfr_and_rotation_and_upscale_warnings(mariage):
    vfr = mariage / "SOIREE" / "vfr.MP4"
    ff("-f", "lavfi", "-i", "testsrc=s=640x360:r=25:d=4", "-vf", "select='lt(mod(n,5),3)'",
       "-fps_mode", "vfr", "-c:v", "libx264", "-an", str(vfr))
    rotated = mariage / "SOIREE" / "rot.MP4"
    ff("-display_rotation", "90", "-i", str(mariage / SONY), "-c", "copy", str(rotated))
    assert probe(rotated).rotation in (90, 270)
    issues = pf.preflight(Project(mariage), dl_of(
        Segment("s001", "SOIREE/vfr.MP4", 0, 1), Segment("s002", "SOIREE/rot.MP4", 0, 1)), "final")
    assert {"VFR", "ORIENTATION", "UPSCALE"} <= codes(issues, "warning")


def test_proxy_missing_then_ok_then_stale(mariage):
    project = Project(mariage)
    dl = dl_of(Segment("s001", SONY, 0, 1))
    assert "PROXY_MISSING" in codes(pf.preflight(project, dl, "proxy"), "warning")
    build_proxies(project, height=180)
    assert "PROXY_MISSING" not in codes(pf.preflight(project, dl, "proxy"))
    src = mariage / SONY
    data = src.read_bytes()
    src.write_bytes(data + b"\0")  # source modifiée : le proxy est périmé
    assert "PROXY_MISSING" in codes(pf.preflight(project, dl, "proxy"), "warning")
