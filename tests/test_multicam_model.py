"""Groupes multicam dans la timeline : angles, synchro réversible, alignement manuel, verrous, audio maître."""

import json

import pytest

from kolnoa_editor.decision_list import DecisionList, Segment
from kolnoa_editor.sync.apply import plan_audio_sync, split_discontinuous
from kolnoa_editor.sync.audio import Segment as SyncSegment, SyncEstimate
from kolnoa_editor.sync.manual import align_manual, solve_points
from kolnoa_editor.sync.metadata import metadata_hint
from kolnoa_editor.timeline import (
    AddAngle, AddGroup, Angle, LockedError, MulticamGroup, RemoveAngle, RemoveGroup, Session, SetAngleSync,
    Timeline, TimelineError, TimelineExportError, Transaction, accept_angle, add_multicam_clip, execute,
    from_decision_list, lock_angle, nudge_angle, op_from_dict, switch_angle, to_decision_list)

WAV = "AUDIO/recorder.wav"
SONY = "MAIRIE/A7IV/20261011_C4837.MP4"
OSMO = "MAIRIE/OP4/DJI_20261011100000_0001_D.MP4"
DRONE = "MAIRIE/DRONE/DJI_20261011100000_0002_D.MP4"


def group_dict(**over):
    return {"id": "g001", "master": "REC", "name": "Houppa", "angles": [
        {"id": "REC", "media": WAV, "kind": "audio", "status": "reference", "method": "reference", "confidence": 1.0},
        {"id": "A7", "media": SONY, "status": "ok", "method": "audio", "confidence": 0.95, "offset": 12.0},
        {"id": "OSMO", "media": OSMO, "status": "incertain", "method": "audio", "confidence": 0.5, "offset": 3.0},
        {"id": "DRONE", "media": DRONE, "has_audio": False}], **over}


def tl_with_group():
    tl = from_decision_list(DecisionList(title="T", resolution=(640, 360), segments=[Segment("s001", SONY, 0, 4)]))
    execute(tl, AddGroup(group_dict()))
    return tl


# --- structure ----------------------------------------------------------------
def test_group_validation():
    tl = tl_with_group()
    tl.validate()
    for mutate in (lambda t: setattr(t.groups[0], "master", "ZZZ"),
                   lambda t: setattr(t.groups[0].angles[0], "has_audio", False),
                   lambda t: setattr(t.groups[0].angles[1], "status", "parfait"),
                   lambda t: setattr(t.groups[0].angles[1], "confidence", 1.5),
                   lambda t: t.groups[0].angles.append(Angle("A7", SONY)),
                   lambda t: setattr(t.groups[0].angles[1], "range", [5, 2])):
        t = tl_with_group()
        mutate(t)
        with pytest.raises(TimelineError):
            t.validate()


def test_angle_axis_mapping_with_drift_is_invertible():
    a = Angle("X", SONY, offset=12.0, drift_ppm=300.0)
    assert a.to_group(100.0) == pytest.approx(12.0 + 100.03)
    assert a.to_angle(a.to_group(77.7)) == pytest.approx(77.7)


def test_old_timelines_without_groups_still_load():
    d = from_decision_list(DecisionList(title="T", segments=[Segment("s001", SONY, 0, 4)])).to_dict()
    del d["groups"]
    assert Timeline.from_dict(d).groups == []


def test_group_ops_roundtrip_and_serialize():
    tl = tl_with_group()
    before = tl.canonical()
    inv = execute(tl, AddAngle("g001", {"id": "CANON", "media": "SOIREE/c.mp4"}))
    assert tl.group("g001").angle("CANON").status == "unsynced"
    execute(tl, inv)
    assert tl.canonical() == before
    op = Transaction([SetAngleSync("g001", "A7", {"offset": 1.0}), RemoveAngle("g001", "DRONE")], "x")
    assert op_from_dict(json.loads(json.dumps(op.to_dict()))).to_dict() == op.to_dict()


def test_master_angle_is_protected_and_used_groups_cannot_be_removed():
    tl = tl_with_group()
    with pytest.raises(TimelineError, match="maître"):
        execute(tl, SetAngleSync("g001", "REC", {"offset": 2.0}))
    with pytest.raises(TimelineError, match="maître"):
        execute(tl, RemoveAngle("g001", "REC"))
    execute(tl, add_multicam_clip(tl, "g001", "A7", "V1", 100, 50, group_in=30.0))
    with pytest.raises(TimelineError, match="utilisé"):
        execute(tl, RemoveGroup("g001"))
    with pytest.raises(TimelineError, match="utilisé"):
        execute(tl, RemoveAngle("g001", "A7"))


# --- verrou et alignement manuel ---------------------------------------------------
def test_locked_angle_refuses_every_sync_change_but_can_be_unlocked():
    tl = tl_with_group()
    execute(tl, lock_angle(tl, "g001", "A7"))
    for op in (nudge_angle(tl, "g001", "A7", 1), SetAngleSync("g001", "A7", {"status": "impossible"}),
               align_manual(tl, "g001", "A7", [[10.0, 20.0]])):
        with pytest.raises(LockedError):
            execute(tl, op)
    assert tl.group("g001").angle("A7").offset == 12.0
    execute(tl, lock_angle(tl, "g001", "A7", False))
    execute(tl, nudge_angle(tl, "g001", "A7", 1))


def test_frame_by_frame_nudge_is_exact_and_undoable():
    tl = tl_with_group()
    undo = [execute(tl, nudge_angle(tl, "g001", "A7", +1)) for _ in range(3)]
    a = tl.group("g001").angle("A7")
    assert a.offset == pytest.approx(12.12) and a.status == "manual" and a.method == "manual" and a.confidence == 1.0
    undo.append(execute(tl, nudge_angle(tl, "g001", "A7", -5)))
    assert tl.group("g001").angle("A7").offset == pytest.approx(11.92)
    for inv in reversed(undo):
        execute(tl, inv)
    a = tl.group("g001").angle("A7")
    assert a.offset == pytest.approx(12.0) and a.status == "ok" and a.method == "audio"


def test_nudge_roundtrip_restores_the_exact_state():
    tl = tl_with_group()
    before = tl.canonical()
    inv = execute(tl, nudge_angle(tl, "g001", "A7", -2))
    execute(tl, inv)
    assert tl.canonical() == before


def test_one_manual_point_gives_the_offset_two_points_give_the_drift():
    one = solve_points([[10.0, 25.0]])
    assert one.offset == pytest.approx(15.0) and not one.drift_measured
    two = solve_points([[0.0, 5.0], [600.0, 605.18]])           # 300 ppm sur 10 min
    assert two.offset == pytest.approx(5.0) and two.drift_ppm == pytest.approx(300.0, abs=0.1) and two.drift_measured


def test_manual_points_too_close_or_absurd_are_refused():
    with pytest.raises(TimelineError, match="trop proches"):
        solve_points([[10.0, 20.0], [12.0, 22.0]])
    with pytest.raises(TimelineError, match="invraisemblable"):
        solve_points([[0.0, 0.0], [100.0, 130.0]])
    with pytest.raises(TimelineError):
        solve_points([[5.0, 6.0], [5.0, 7.0]])
    with pytest.raises(TimelineError):
        solve_points([])


def test_align_manual_snaps_to_the_frame_grid_and_marks_the_angle_usable():
    tl = tl_with_group()
    execute(tl, align_manual(tl, "g001", "DRONE", [[3.004, 41.0]], snap_to_frames=True))
    a = tl.group("g001").angle("DRONE")
    assert a.offset == pytest.approx(38.0)                                   # 41 − 3,004 = 37,996 → image la plus proche
    assert abs(a.offset * 25 - round(a.offset * 25)) < 1e-6
    assert a.usable and a.points == [[3.004, 41.0]]


# --- plans multicam, audio maître ----------------------------------------------------
def test_unreliable_angles_cannot_be_used_until_a_human_accepts_them():
    tl = tl_with_group()
    with pytest.raises(TimelineError, match="pas synchronisé de façon fiable"):
        add_multicam_clip(tl, "g001", "OSMO", "V1", 100, 50, 10.0)
    with pytest.raises(TimelineError, match="pas synchronisé"):
        add_multicam_clip(tl, "g001", "DRONE", "V1", 100, 50, 10.0)       # drone jamais synchronisé
    execute(tl, accept_angle(tl, "g001", "OSMO"))
    execute(tl, add_multicam_clip(tl, "g001", "OSMO", "V1", 100, 50, 10.0))
    with pytest.raises(TimelineError):
        accept_angle(tl, "g001", "DRONE")                                   # rien à valider : jamais synchronisé


def test_switching_angle_keeps_the_same_instant_and_the_master_audio():
    tl = tl_with_group()
    execute(tl, accept_angle(tl, "g001", "OSMO"))
    execute(tl, add_multicam_clip(tl, "g001", "A7", "V1", 100, 50, group_in=30.0, clip_id="s010"))
    c = tl.clip("s010")
    assert c.src_in == pytest.approx(18.0) and c.audio == "group" and c.media == SONY      # 30 − 12
    inv = execute(tl, switch_angle(tl, "s010", "OSMO"))
    c = tl.clip("s010")
    assert (c.angle, c.media, c.audio) == ("OSMO", OSMO, "group")                           # le son ne change pas
    assert c.src_in == pytest.approx(27.0)                                                  # 30 − 3 : même instant
    assert (c.start, c.duration) == (100, 50)
    execute(tl, inv)
    assert tl.clip("s010").angle == "A7" and tl.clip("s010").src_in == pytest.approx(18.0)


def test_switch_angle_with_drift_uses_both_clocks():
    tl = tl_with_group()
    execute(tl, SetAngleSync("g001", "OSMO", {"status": "ok", "drift_ppm": 1000.0}))
    execute(tl, add_multicam_clip(tl, "g001", "A7", "V1", 100, 50, group_in=100.0, clip_id="s010"))
    execute(tl, switch_angle(tl, "s010", "OSMO"))
    osmo = tl.group("g001").angle("OSMO")
    assert osmo.to_group(tl.clip("s010").src_in) == pytest.approx(100.0, abs=1e-5)


def test_multicam_clip_must_match_its_angle_media_and_cannot_be_exported_blindly():
    tl = tl_with_group()
    execute(tl, add_multicam_clip(tl, "g001", "A7", "V1", 100, 50, 30.0, clip_id="s010"))
    tl.clip("s010").media = OSMO
    with pytest.raises(TimelineError, match="différent"):
        tl.validate()
    tl.clip("s010").media = SONY
    with pytest.raises(TimelineExportError, match="multicam"):
        to_decision_list(tl)


def test_non_regression_pr_a_timeline_without_groups_exports_as_before():
    tl = from_decision_list(DecisionList(title="T", resolution=(640, 360), segments=[Segment("s001", SONY, 0, 4)]))
    dl, warnings = to_decision_list(tl)
    assert dl.segments[0].audio == "source" and warnings == []


# --- application des résultats de synchro ----------------------------------------------------
def est(status="ok", offset=12.5, conf=0.9, **kw):
    return SyncEstimate(status, conf, offset=offset, drift_ppm=kw.get("drift", 0.0), notes=kw.get("notes", []))


def test_plan_audio_sync_is_one_undo_step_and_respects_locks_and_manual_work():
    tl = tl_with_group()
    execute(tl, lock_angle(tl, "g001", "A7"))
    execute(tl, align_manual(tl, "g001", "DRONE", [[1.0, 9.0]]))
    report = plan_audio_sync(tl, "g001", {"A7": est(offset=99.0), "OSMO": est(offset=3.2, drift=120.0),
                                          "DRONE": est(offset=77.0), "REC": est()})
    assert any("verrouillé" in l for l in report.lines) and any("réglé à la main" in l for l in report.lines)
    before = tl.canonical()
    inv = execute(tl, report.op)
    g = tl.group("g001")
    assert g.angle("A7").offset == 12.0 and g.angle("DRONE").offset == 8.0              # intacts
    assert g.angle("OSMO").offset == pytest.approx(3.2) and g.angle("OSMO").status == "ok"
    execute(tl, inv)
    assert tl.canonical() == before


def test_impossible_never_overwrites_a_previous_offset_but_flags_the_angle():
    tl = tl_with_group()
    report = plan_audio_sync(tl, "g001", {"OSMO": est("impossible", offset=0.0, conf=0.0, notes=["aucune correspondance"])})
    execute(tl, report.op)
    o = tl.group("g001").angle("OSMO")
    assert o.status == "impossible" and o.offset == 3.0 and o.confidence == 0.0 and not o.usable


def test_a_discontinuous_clip_becomes_several_angle_entries_with_ranges():
    tl = tl_with_group()
    e = est("discontinu", conf=0.8)
    e.segments = [SyncSegment(0.0, 80.0, 20.0, 0.0, 4), SyncSegment(80.0, 160.0, 50.0, 0.0, 4)]
    rep = split_discontinuous(tl, "g001", "A7", e)
    execute(tl, rep.op)
    g = tl.group("g001")
    assert g.angle("A7").range == [0.0, 80.0] and g.angle("A7").offset == 20.0
    assert g.angle("A7.2").range == [80.0, 160.0] and g.angle("A7.2").media == SONY and g.angle("A7.2").status == "incertain"
    assert not g.angle("A7.2").usable and g.angle("A7.2").confidence <= 0.69


def test_metadata_is_only_a_hint():
    assert metadata_hint("2026-10-11T10:00:00", "2026-10-11T10:02:30") == (150.0, 300.0)
    assert metadata_hint(None, "2026-10-11T10:02:30") is None
    assert metadata_hint("2026-10-11T10:00:00", "n'importe quoi") is None


def test_multicam_edits_survive_a_restart_with_undo(tmp_path):
    s = Session.create(tmp_path / "s", from_decision_list(DecisionList(title="T", segments=[Segment("s001", SONY, 0, 4)])))
    s.do(AddGroup(group_dict()))
    s.do(nudge_angle(s.tl, "g001", "A7", 2))
    s.do(add_multicam_clip(s.tl, "g001", "A7", "V1", 100, 50, 30.0, clip_id="s010"))
    final = s.tl.canonical()
    s.undo()
    r = Session.open(tmp_path / "s")
    r.redo()
    assert r.tl.canonical() == final
