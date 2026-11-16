"""PR C3b : chutier I/O — repérer des plans courts, les poser sur la timeline sans rien casser."""

import pytest

from kolnoa_editor.decision_list import DecisionList, Segment
from kolnoa_editor.timeline import (AddTrack, Timeline, TimelineError, adjust_subclip, execute, from_decision_list,
                                    mark_subclip, op_from_dict, place_subclip, remove_subclip, search_bin, Session)
from tests.test_timeline_edit import OSMO, SONY, base, clips


def tl_bin():
    tl = base()                                   # V1 s001 [0,100) s002 [100,200) ; A1 s003 [0,200) ; V2 libre
    execute(tl, mark_subclip(tl, OSMO, 12.0, 15.0, "plan de coupe fleurs", tags=["coupe"]))
    execute(tl, mark_subclip(tl, SONY, 3.0, 4.0, "regard", tags=["émotion"], note="la mariée sourit"))
    return tl


def roundtrip_ok(tl, op):
    work, fresh = tl.copy(), tl.copy()
    before = work.canonical()
    inv = execute(work, op)
    after = work.canonical()
    execute(work, inv)
    assert work.canonical()["clips"] == before["clips"] and work.canonical()["bin"] == before["bin"]
    execute(fresh, op_from_dict(op.to_dict()))
    assert fresh.canonical() == after


def test_mark_and_validate():
    tl = tl_bin()
    assert [b.id for b in tl.bin] == ["b001", "b002"] and tl.bin[0].length == 3.0
    for args in ((OSMO, 5.0, 5.0), (OSMO, 6.0, 2.0), (OSMO, -1.0, 2.0)):
        with pytest.raises(TimelineError):
            mark_subclip(tl, *args)
    with pytest.raises(TimelineError):
        mark_subclip(tl, OSMO, 1.0, 20.0, source_len=10.0)       # sortie au-delà de la fin du média
    mark_subclip(tl, OSMO, 1.0, 20.0, source_len=30.0)


def test_adjust_remove_and_search_are_reversible():
    tl = tl_bin()
    roundtrip_ok(tl, adjust_subclip(tl, "b001", src_out=16.0, name="fleurs"))
    roundtrip_ok(tl, remove_subclip(tl, "b002"))
    with pytest.raises(TimelineError):
        adjust_subclip(tl, "b001", src_in=20.0)                   # entrée après la sortie
    with pytest.raises(TimelineError):
        adjust_subclip(tl, "b001")
    assert search_bin(tl, "fleurs") == ["b001"] and search_bin(tl, "sourit") == ["b002"]
    assert search_bin(tl, tag="émotion") == ["b002"] and search_bin(tl, media=OSMO) == ["b001"]


def test_place_on_cutaway_track_moves_nothing_else():
    tl = tl_bin()
    before_v1, before_a1 = clips(tl, "V1"), clips(tl, "A1")
    execute(tl, place_subclip(tl, "b001", "V2", 50))
    (c,) = tl.clips_on("V2")
    assert (c.media, c.start, c.duration, c.src_in, c.label) == (OSMO, 50, 75, 12.0, "plan de coupe fleurs")
    assert clips(tl, "V1") == before_v1 and clips(tl, "A1") == before_a1      # rien d'autre n'a bougé
    assert tl.bin[0].src_in == 12.0                                            # le chutier reste intact


def test_place_with_linked_sound_and_undo_in_one_step():
    tl = tl_bin()
    op = place_subclip(tl, "b002", "V2", 20, audio_track="A1")
    # A1 est occupée par s003 : le recouvrement coupe son son dessous, le tout dans une seule transaction
    work = tl.copy()
    inv = execute(work, op)
    v = work.clips_on("V2")[0]
    a = [c for c in work.clips_on("A1") if c.link == v.link][0]
    assert v.link and v.audio == "mute" and (a.start, a.duration, a.src_in) == (20, 25, 3.0)
    execute(work, inv)
    assert clips(work, "A1") == clips(tl, "A1") and work.clips_on("V2") == []


def test_insert_mode_pushes_following_clips():
    tl = tl_bin()
    execute(tl, place_subclip(tl, "b002", "V1", 100, mode="insert"))
    assert [(i, s, e) for i, s, e, _ in clips(tl, "V1")][:3] == [("s001", 0, 100), ("s004", 100, 125), ("s002", 125, 225)]


def test_track_kind_mismatch_and_unknown_ids():
    tl = tl_bin()
    with pytest.raises(TimelineError):
        place_subclip(tl, "b001", "A1", 0)                         # image sur une piste audio
    with pytest.raises(TimelineError):
        place_subclip(tl, "b999", "V2", 0)
    with pytest.raises(TimelineError):
        place_subclip(tl, "b001", "V2", 0, audio_track="V1")        # V1 n'est pas une piste audio


def test_bin_survives_session_reopen(tmp_path):
    tl = tl_bin()
    s = Session.create(tmp_path / "p", tl)
    s.do(mark_subclip(s.tl, OSMO, 20.0, 22.0, "ciel"), "I/O")
    s.do(place_subclip(s.tl, "b003", "V2", 0), "pose")
    s.undo()
    again = Session.open(tmp_path / "p")
    assert [b.id for b in again.tl.bin] == ["b001", "b002", "b003"] and again.tl.clips_on("V2") == []
