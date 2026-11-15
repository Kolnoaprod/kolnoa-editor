"""Régressions de fiabilité (issue #1, P0). Chacun de ces tests échoue sur le socle initial (0d8bbe2)."""

import os
import tempfile
from pathlib import Path

import pytest
from helpers import copy_wedding, ff

from kolnoa_editor.cli import main
from kolnoa_editor.decision_list import DecisionList, Segment
from kolnoa_editor.probe import probe
from kolnoa_editor.project import Project
from kolnoa_editor.renderer import render
from kolnoa_editor.scanner import load_library

SONY = "MAIRIE/A7IV/20261011_C4837.MP4"
DRONE = "SOIREE/DRONE/DJI_20261108192622_0015_D.MP4"


def rels(root):
    return {f.rel for f in load_library(Project(root))}


def test_scan_subdir_keeps_rest_of_library(mariage):
    main(["scan", str(mariage)])
    full = rels(mariage)
    main(["scan", str(mariage), "--subdir", "MAIRIE"])
    assert rels(mariage) == full


def test_rescan_keeps_probe_metadata(mariage):
    main(["scan", str(mariage), "--probe"])
    before = {f.rel: (f.duration, f.has_audio) for f in load_library(Project(mariage))}
    assert before[SONY][0] == pytest.approx(6.0, abs=0.1)
    main(["scan", str(mariage)])  # re-scan sans --probe
    after = {f.rel: (f.duration, f.has_audio) for f in load_library(Project(mariage))}
    assert after == before


def test_proxies_do_not_rely_on_stale_library(mariage):
    main(["scan", str(mariage)])
    new = mariage / "MAIRIE" / "A7IV" / "20261011_C4838.MP4"
    ff("-f", "lavfi", "-i", "testsrc=s=640x360:r=25:d=2", "-c:v", "libx264", "-an", str(new))
    main(["proxies", str(mariage), "--height", "180"])
    assert Project(mariage).proxy_path("MAIRIE/A7IV/20261011_C4838.MP4").is_file()


def test_proxy_rebuilt_when_height_changes(mariage):
    main(["proxies", str(mariage), "--height", "360"])
    main(["proxies", str(mariage), "--height", "180"])
    assert probe(Project(mariage).proxy_path(SONY)).height == 180


def test_proxy_rebuilt_when_source_replaced_with_older_mtime(mariage):
    project = Project(mariage)
    main(["proxies", str(mariage), "--height", "180"])
    assert probe(project.proxy_path(SONY)).duration == pytest.approx(6.0, abs=0.2)
    src = mariage / SONY
    src.unlink()
    ff("-f", "lavfi", "-i", "testsrc=s=1280x720:r=25:d=3", "-c:v", "libx264", "-an", str(src))
    os.utime(src, (1000000000, 1000000000))  # Drive/copie qui restaure une vieille date
    main(["proxies", str(mariage), "--height", "180"])
    assert probe(project.proxy_path(SONY)).duration == pytest.approx(3.0, abs=0.2)


def test_render_survives_apostrophes_in_paths(wedding, tmp_path, monkeypatch):
    root = copy_wedding(wedding, tmp_path / "Mariage d'Elie et Rebecca")
    tmp = tmp_path / "o'brien tmp"
    tmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    dl = DecisionList(
        title="Apostrophe",
        resolution=(640, 360),
        segments=[Segment("s001", SONY, 0, 1.0), Segment("s002", DRONE, 0, 1.0)],
    )
    out = render(Project(root), dl, "final")
    assert probe(out).duration == pytest.approx(2.0, abs=0.25)
