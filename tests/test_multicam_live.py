"""PR C3a : touches 1 à 9 — changements d'angle pendant la lecture, annulables d'un seul geste."""

import pytest

from kolnoa_editor.decision_list import DecisionList, Segment
from kolnoa_editor.timeline import (AddGroup, Session, TimelineError, add_multicam_clip, angle_for_key, cut_to_angle,
                                    execute, from_decision_list, key_angles, op_from_dict, record_angle_switches)
from tests.test_multicam_model import SONY, group_dict


def tl_cam():
    """Groupe g001 : touches 1=A7 (ok), 2=OSMO (incertain), 3=DRONE (non synchronisé). Un plan A7 sur V1 [100,300)."""
    tl = from_decision_list(DecisionList(title="T", resolution=(640, 360), segments=[Segment("s001", SONY, 0, 4)]))
    execute(tl, AddGroup(group_dict()))
    tl.clips.clear()
    execute(tl, add_multicam_clip(tl, "g001", "A7", "V1", 100, 200, group_in=30.0, clip_id="s010"))
    return tl


def shown(tl):
    return [(c.start, c.end, c.angle) for c in tl.clips_on("V1")]


def test_keys_follow_image_angles_order():
    tl = tl_cam()
    assert key_angles(tl, "g001") == ["A7", "OSMO", "DRONE"]       # le micro REC (audio) n'a pas de touche
    assert angle_for_key(tl, "g001", 2) == "OSMO"
    for bad in (0, 4, 10):
        with pytest.raises(TimelineError):
            angle_for_key(tl, "g001", bad)


def test_one_key_cuts_and_keeps_the_same_group_instant():
    tl = tl_cam()
    g_instant_before = tl.group("g001").angle("A7").to_group(tl.clip("s010").src_in + (200 - 100) / tl.fps)
    tl.group("g001").angle("OSMO").status = "ok"
    execute(tl, cut_to_angle(tl, "V1", 200, "OSMO"))
    assert shown(tl) == [(100, 200, "A7"), (200, 300, "OSMO")]
    right = tl.clips_on("V1")[1]
    assert tl.group("g001").angle("OSMO").to_group(right.src_in) == pytest.approx(g_instant_before)
    assert right.audio == "group"


def test_take_is_one_undo_step_and_journal_replays():
    tl = tl_cam()
    tl.group("g001").angle("OSMO").status = "ok"
    op = record_angle_switches(tl, "V1", [(250, "A7"), (150, "OSMO"), (200, "A7"), (200, "A7")])
    # envoyé dans le désordre : 150 → OSMO, 200 → A7, 250 → A7 (ignoré : déjà A7)
    work, fresh = tl.copy(), tl.copy()
    inv = execute(work, op)
    assert shown(work) == [(100, 150, "A7"), (150, 200, "OSMO"), (200, 300, "A7")]
    execute(work, inv)
    assert work.canonical()["clips"] == tl.canonical()["clips"]
    execute(fresh, op_from_dict(op.to_dict()))
    assert shown(fresh) == [(100, 150, "A7"), (150, 200, "OSMO"), (200, 300, "A7")]


def test_unsynced_angle_cancels_the_whole_take():
    tl = tl_cam()
    tl.group("g001").angle("OSMO").status = "ok"
    before = tl.canonical()
    with pytest.raises(TimelineError):
        record_angle_switches(tl, "V1", [(150, "OSMO"), (200, "DRONE")])    # drone jamais synchronisé
    assert tl.canonical() == before


def test_refusals():
    tl = tl_cam()
    with pytest.raises(TimelineError):
        cut_to_angle(tl, "V1", 50, "A7")                # aucun plan à cet endroit
    with pytest.raises(TimelineError):
        cut_to_angle(tl, "V1", 150, "A7")               # déjà sur A7
    with pytest.raises(TimelineError):
        record_angle_switches(tl, "V1", [])
    tl.track("V1").locked = True
    tl.group("g001").angle("OSMO").status = "ok"
    with pytest.raises(TimelineError):
        cut_to_angle(tl, "V1", 150, "OSMO")             # piste verrouillée


def test_key_on_clip_start_switches_without_cutting():
    tl = tl_cam()
    tl.group("g001").angle("OSMO").status = "ok"
    execute(tl, cut_to_angle(tl, "V1", 100, "OSMO"))
    assert shown(tl) == [(100, 300, "OSMO")]


def test_session_undo_redo_of_a_take(tmp_path):
    tl = tl_cam()
    tl.group("g001").angle("OSMO").status = "ok"
    s = Session.create(tmp_path / "p", tl)
    original = s.tl.canonical()["clips"]
    s.do(record_angle_switches(s.tl, "V1", [(150, "OSMO"), (200, "A7")]), "prise multicam")
    assert shown(s.tl) == [(100, 150, "A7"), (150, 200, "OSMO"), (200, 300, "A7")]
    s.undo()                                              # toute la prise d'un coup
    assert s.tl.canonical()["clips"] == original
    s.redo()
    again = Session.open(tmp_path / "p")                  # le journal rejoue la prise à la réouverture
    assert shown(again.tl) == [(100, 150, "A7"), (150, 200, "OSMO"), (200, 300, "A7")]
