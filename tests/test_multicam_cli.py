"""Chaîne complète en ligne de commande, avec de vrais fichiers : enregistreur WAV, caméra avec son, drone muet."""

import json

import pytest

from kolnoa_editor.cli import main
from kolnoa_editor.decision_list import DecisionList, Segment
from kolnoa_editor.project import Project
from kolnoa_editor.sync.synth import (extract, luma_scene, mux_audio, speech_like, write_gray_video, write_wav)
from kolnoa_editor.timeline import Session

FS = 8000
CAM_OFFSET = 25.5
DRONE_OFFSET = 30.0
SHARED = [40.0, 70.0, 95.0, 120.0]            # événements lumineux communs (axe du groupe)


@pytest.fixture()
def shoot(mariage, tmp_path):
    master = speech_like(160, seed=5, rate=FS)
    write_wav(mariage / "SYNC" / "rec.wav", master, FS)
    cam_audio = extract(master, FS, CAM_OFFSET, 100.0, out_rate=FS, snr_db=20, seed=1)
    cam_wav = write_wav(tmp_path / "cam.wav", cam_audio, FS)
    cam_video = write_gray_video(tmp_path / "cam_v.mp4", luma_scene(100, 10.0, 31, events=[e - CAM_OFFSET for e in SHARED if e > CAM_OFFSET + 3]), 10.0)
    (mariage / "SYNC").mkdir(exist_ok=True)
    mux_audio(cam_video, cam_wav, mariage / "SYNC" / "cam.mp4")
    write_gray_video(mariage / "SYNC" / "drone.mp4",
                     luma_scene(100, 10.0, 32, events=[e - DRONE_OFFSET for e in SHARED if e > DRONE_OFFSET + 3]), 10.0)
    dl = DecisionList(title="T", resolution=(640, 360), segments=[Segment("s001", "SYNC/cam.mp4", 0.0, 4.0)])
    dl_file = tmp_path / "v1.json"
    dl.save(dl_file)
    root = str(mariage)
    assert main(["timeline-import", root, str(dl_file)]) == 0
    assert main(["multicam-create", root, "--master", "REC=SYNC/rec.wav", "--angle", "CAM=SYNC/cam.mp4",
                 "--angle", "DRONE=SYNC/drone.mp4", "--name", "Houppa"]) == 0
    return root, Project(mariage)


def angle(project, aid):
    return Session.open(project.timeline_dir).tl.group("g001").angle(aid)


def test_create_detects_audio_and_video(shoot):
    _, project = shoot
    g = Session.open(project.timeline_dir).tl.group("g001")
    assert [(a.id, a.kind, a.has_audio) for a in g.angles] == [("REC", "audio", True), ("CAM", "video", True), ("DRONE", "video", False)]
    assert g.angle("REC").status == "reference" and g.angle("DRONE").status == "unsynced"


def test_dry_run_changes_nothing_then_real_sync_is_undoable(shoot, capsys):
    root, project = shoot
    before = (project.timeline_dir / "history.jsonl").read_text()
    assert main(["multicam-sync", root, "g001", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "CAM: ok" in out and "simulation" in out and "EXPÉRIMENTALE" in out
    assert (project.timeline_dir / "history.jsonl").read_text() == before
    assert main(["multicam-sync", root, "g001"]) == 0
    cam = angle(project, "CAM")
    assert cam.status == "ok" and cam.method == "audio" and abs(cam.offset - CAM_OFFSET) < 0.06
    drone = angle(project, "DRONE")
    assert drone.status == "unsynced" and not drone.usable          # le drone n'est JAMAIS posé sans demande explicite
    assert main(["timeline-undo", root]) == 0
    assert angle(project, "CAM").status == "unsynced"


def test_visual_proposal_is_accepted_explicitly_then_validated_and_adjusted_by_hand(shoot, capsys):
    root, project = shoot
    assert main(["multicam-sync", root, "g001"]) == 0
    assert main(["multicam-sync", root, "g001", "--accept-visual", "1"]) == 0
    d = angle(project, "DRONE")
    assert d.method == "visual" and d.status == "incertain" and not d.usable
    assert abs(d.offset - DRONE_OFFSET) < 0.4 and d.confidence <= 0.6
    assert main(["multicam-accept", root, "g001", "DRONE"]) == 0
    assert angle(project, "DRONE").usable
    before = angle(project, "DRONE").offset
    assert main(["multicam-nudge", root, "g001", "DRONE", "3"]) == 0
    assert angle(project, "DRONE").offset == pytest.approx(before + 0.12)
    assert main(["multicam-lock", root, "g001", "DRONE"]) == 0
    assert main(["multicam-nudge", root, "g001", "DRONE", "1"]) == 2          # verrouillé
    capsys.readouterr()
    assert main(["multicam-at", root, "g001", "100"]) == 0
    at = capsys.readouterr().out
    assert "CAM: 74.500 s" in at and "REC: 100.000 s" in at


def test_manual_two_point_alignment_via_cli_and_locked_angle_survives_sync(shoot):
    root, project = shoot
    assert main(["multicam-align", root, "g001", "CAM", "--point", "10", "35.5", "--point", "90", "115.5"]) == 0
    cam = angle(project, "CAM")
    assert cam.status == "manual" and cam.offset == pytest.approx(25.5, abs=1e-6) and cam.drift_ppm == pytest.approx(0, abs=0.01)
    assert main(["multicam-lock", root, "g001", "CAM"]) == 0
    assert main(["multicam-sync", root, "g001"]) == 0
    assert angle(project, "CAM").offset == pytest.approx(25.5, abs=1e-6) and angle(project, "CAM").locked
    assert main(["multicam-align", root, "g001", "CAM", "--point", "10", "35.5"]) == 2


def test_bad_config_and_unknown_group_fail_cleanly(shoot, tmp_path):
    root, _ = shoot
    cfg = tmp_path / "bad.json"
    cfg.write_text(json.dumps({"seuil_inconnu": 1}))
    assert main(["multicam-sync", root, "g001", "--config", str(cfg)]) == 2
    assert main(["multicam-sync", root, "g999"]) == 2
