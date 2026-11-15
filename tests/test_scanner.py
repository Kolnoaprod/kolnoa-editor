from datetime import datetime

from kolnoa_editor.project import Project
from kolnoa_editor.scanner import load_library, parse_start, save_library, scan, summarize


def test_scan_classifies_devices_and_kinds(wedding):
    files = {f.rel: f for f in scan(Project(wedding))}
    assert set(files) == {
        "MAIRIE/A7IV/20261011_C4837.MP4",
        "SOIREE/DRONE/DJI_20261108192622_0015_D.MP4",
        "SOIREE/CANON/A_0009C238A261108_113335HL_CANON.MP4",
        "SOIREE/AUDIO/4CH000I.wav",
        "musique.mp3",
    }
    assert files["MAIRIE/A7IV/20261011_C4837.MP4"].device == "A7IV"
    assert files["SOIREE/DRONE/DJI_20261108192622_0015_D.MP4"].device == "DRONE"
    assert files["SOIREE/CANON/A_0009C238A261108_113335HL_CANON.MP4"].device == "CANON"
    assert files["SOIREE/AUDIO/4CH000I.wav"].kind == "audio"
    assert files["MAIRIE/A7IV/20261011_C4837.MP4"].group == "MAIRIE"


def test_parse_start_from_filenames():
    dji, origin = parse_start("DJI_20261108192622_0015_D.MP4", 0)
    assert dji == datetime(2026, 11, 8, 19, 26, 22) and origin == "filename"
    canon, origin = parse_start("A_0009C238A261108_113335HL_CANON.MP4", 0)
    assert canon == datetime(2026, 11, 8, 11, 33, 35) and origin == "filename"
    sony, origin = parse_start("20261011_C4837.MP4", datetime(2026, 10, 11, 13, 6, 39).timestamp())
    assert sony == datetime(2026, 10, 11, 13, 6, 39) and origin == "mtime"


def test_library_roundtrip_and_summary(wedding):
    project = Project(wedding)
    files = scan(project)
    save_library(project, files)
    assert [f.rel for f in load_library(project)] == [f.rel for f in files]
    assert summarize(files)["MAIRIE / A7IV"]["files"] == 1


def test_scan_ignores_kolnoa_data_dir(wedding):
    project = Project(wedding)
    project.ensure()
    (project.data / "junk.mp4").write_bytes(b"x")
    assert not any(".kolnoa" in f.rel for f in scan(project))
