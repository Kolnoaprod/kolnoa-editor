"""Transitions posées sur les coupes : poser, étirer, faire glisser, annuler, et suivre les scissions."""

import random

import pytest

from kolnoa_editor.decision_list import DecisionList, Segment
from kolnoa_editor.timeline import (
    AddTransition, LockedError, MoveClip, RemoveClip, RemoveTransition, Session, SetClipProps, SetTransition, SplitClip,
    Timeline, TimelineError, Transaction, TrimClip, add_transition, execute, from_decision_list,
    resize_transition, ripple_delete, slide_transition, to_decision_list)

SONY = "MAIRIE/A7IV/20261011_C4837.MP4"


def tl3():  # s001 0-50, s002 50-100, s003 100-125 (25 i/s)
    return from_decision_list(DecisionList(title="T", resolution=(640, 360), segments=[
        Segment("s001", SONY, 0.0, 2.0), Segment("s002", SONY, 2.0, 4.0), Segment("s003", SONY, 4.0, 5.0)]))


def with_fade(seconds=0.5, align="center"):
    tl = tl3()
    execute(tl, add_transition(tl, "s001", "fade", seconds=seconds, align=align))
    return tl


def test_add_transition_on_the_next_cut_without_changing_the_film_length():
    tl = with_fade(0.5)
    t = tl.transitions[0]
    assert (t.id, t.from_clip, t.to_clip, t.duration) == ("t001", "s001", "s002", 12)   # 0,5 s = 12,5 -> 12 i
    assert tl.end_frame == 125
    assert tl.window(t) == (44, 56)                       # centrée sur la coupe de l'image 50


def test_resize_by_seconds_or_by_frames_is_how_the_prompt_and_the_mouse_both_work():
    tl = with_fade(0.5)
    execute(tl, resize_transition(tl, "t001", seconds=1.0))        # « mets 1 seconde de fondu »
    assert tl.transitions[0].duration == 25
    execute(tl, resize_transition(tl, "t001", frames=7))           # poignée tirée à la souris
    assert tl.transitions[0].duration == 7 and tl.end_frame == 125
    with pytest.raises(TimelineError):
        resize_transition(tl, "t001", seconds=1.0, frames=5)       # une seule unité à la fois
    with pytest.raises(TimelineError):
        resize_transition(tl, "t001")


def test_resize_is_capped_to_half_the_shortest_clip_and_refusal_changes_nothing():
    tl = with_fade(0.5)
    before = tl.canonical()
    with pytest.raises(TimelineError, match="maximum 25 images"):
        execute(tl, resize_transition(tl, "t001", seconds=2.0))   # 50 i > moitié de 50 i
    assert tl.canonical() == before
    execute(tl, resize_transition(tl, "t001", seconds=1.0))        # 25 i : pile la limite


def test_slide_changes_the_window_not_the_duration():
    tl = with_fade(0.4)                                            # 10 images
    t = tl.transitions[0]
    assert tl.window(t) == (45, 55)
    execute(tl, slide_transition(tl, "t001", "end"))
    assert tl.window(t) == (40, 50)                                # finit sur la coupe
    execute(tl, slide_transition(tl, "t001", "start"))
    assert tl.window(t) == (50, 60)                                # commence sur la coupe
    with pytest.raises(TimelineError):
        execute(tl, slide_transition(tl, "t001", "ailleurs"))


def test_a_clip_cannot_be_asked_for_more_frames_than_it_has_around_two_transitions():
    tl = tl3()
    execute(tl, add_transition(tl, "s001", "fade", frames=10, align="end"))
    execute(tl, add_transition(tl, "s002", "flash", frames=5, align="center"))   # coupe s002/s003 (s003 = 25 i)
    with pytest.raises(TimelineError, match="maximum 12"):
        execute(tl, resize_transition(tl, "t002", frames=13))    # plus que la moitié de s003


def test_one_transition_per_cut_and_only_on_contiguous_clips():
    tl = with_fade()
    with pytest.raises(TimelineError, match="existe déjà"):
        execute(tl, add_transition(tl, "s001", "fadeblack", frames=5))
    with pytest.raises(TimelineError, match="pas de plan contigu"):
        add_transition(tl, "s003", "fade", frames=5)               # dernier plan : pas de coupe après
    with pytest.raises(TimelineError, match="inconnu"):
        execute(tl, add_transition(tl, "s002", "tourbillon", frames=5))


def test_moving_or_trimming_a_cut_under_a_transition_is_refused_not_silently_broken():
    tl = with_fade(0.4)
    before = tl.canonical()
    for op in (Transaction([TrimClip("s002", 50, 30, 2.0), MoveClip("s002", 55)]), TrimClip("s001", 0, 40, 0.0)):
        with pytest.raises(TimelineError, match="contigus"):
            execute(tl, op)
    assert tl.canonical() == before
    execute(tl, Transaction([RemoveTransition("t001"), TrimClip("s001", 0, 40, 0.0), MoveClip("s002", 40)]))
    assert tl.transitions == []                                    # en retirant d'abord : permis


def test_removing_a_clip_takes_its_transitions_and_undo_brings_them_back():
    tl = with_fade(0.4)
    execute(tl, add_transition(tl, "s002", "fadewhite", frames=6))
    before = tl.canonical()
    inv = execute(tl, RemoveClip("s002"))
    assert tl.transitions == []
    execute(tl, inv)
    assert tl.canonical() == before


def test_ripple_delete_and_undo_keep_the_other_transitions():
    tl = with_fade(0.4)
    execute(tl, add_transition(tl, "s002", "fadewhite", frames=6))
    before = tl.canonical()
    inv = execute(tl, ripple_delete(tl, "s002"))
    assert tl.transitions == [] and [c.id for c in tl.clips_on("V1")] == ["s001", "s003"]
    execute(tl, inv)
    assert tl.canonical() == before


def test_split_moves_the_exit_transition_to_the_new_right_half_and_undo_restores():
    tl = tl3()
    execute(tl, add_transition(tl, "s002", "flash", frames=5))     # s002 -> s003
    before = tl.canonical()
    inv = execute(tl, SplitClip("s002", 75))
    t = tl.transitions[0]
    assert (t.from_clip, t.to_clip) == ("s004", "s003")
    execute(tl, inv)
    assert tl.canonical()["transitions"] == before["transitions"]
    assert tl.transitions[0].from_clip == "s002"


def test_joining_a_split_that_carries_a_transition_restores_it_on_redo():
    tl = tl3()
    inv = execute(tl, SplitClip("s002", 75))
    execute(tl, add_transition(tl, "s002", "fade", frames=6))      # transition sur la coupe créée
    s = Session  # noqa: F841
    redo = execute(tl, inv)                                        # l'annulation referme la scission
    assert tl.transitions == [] and len(tl.clips) == 3
    again = execute(tl, redo)                                      # le rétablissement rouvre la coupe
    assert len(tl.clips) == 4
    execute(tl, again)                                             # et re-annuler reste cohérent
    assert len(tl.clips) == 3


def test_locks_protect_transitions():
    tl = with_fade()
    execute(tl, SetClipProps("s002", {"locked": True}))
    with pytest.raises(LockedError):
        execute(tl, resize_transition(tl, "t001", frames=6))
    with pytest.raises(LockedError):
        execute(tl, slide_transition(tl, "t001", "end"))


def test_session_undo_redo_and_restart_with_transitions(tmp_path):
    s = Session.create(tmp_path / "s", tl3())
    s.do(add_transition(s.tl, "s001", "fade", seconds=0.5))
    s.do(resize_transition(s.tl, "t001", seconds=0.8))
    s.do(slide_transition(s.tl, "t001", "end"))
    final = s.tl.canonical()
    s.undo()
    assert s.tl.transitions[0].align == "center" and s.tl.transitions[0].duration == 20
    reopened = Session.open(tmp_path / "s")
    reopened.redo()
    assert reopened.tl.canonical() == final
    reopened.undo(); reopened.undo(); reopened.undo()
    assert reopened.tl.transitions == []


def test_old_timeline_files_without_transitions_still_load():
    d = tl3().to_dict()
    del d["transitions"]
    assert Timeline.from_dict(d).transitions == []


def test_export_warns_that_transitions_are_not_in_the_decision_list():
    _, warnings = to_decision_list(with_fade())
    assert any("transition" in w for w in warnings)


def test_random_transition_edits_always_undo(tmp_path):
    rng = random.Random(3)
    for _ in range(40):
        tl = tl3()
        start = tl.canonical()
        undo = []
        for _ in range(rng.randint(3, 10)):
            ts = tl.transitions
            try:
                kind = rng.choice(["add", "resize", "slide", "type", "split", "ripple"])
                if kind == "add":
                    op = add_transition(tl, rng.choice(["s001", "s002", "s003", "s004"]), rng.choice(["fade", "flash"]),
                                        frames=rng.randint(1, 14), align=rng.choice(["center", "start", "end"]))
                elif kind == "resize" and ts:
                    op = resize_transition(tl, rng.choice(ts).id, frames=rng.randint(1, 20))
                elif kind == "slide" and ts:
                    op = slide_transition(tl, rng.choice(ts).id, rng.choice(["center", "start", "end"]))
                elif kind == "type" and ts:
                    op = SetTransition(rng.choice(ts).id, {"type": rng.choice(["fade", "fadeblack", "fadewhite", "flash"])})
                elif kind == "split":
                    c = rng.choice(tl.clips)
                    op = SplitClip(c.id, rng.randint(c.start + 1, c.end - 1))
                elif kind == "ripple":
                    op = ripple_delete(tl, rng.choice(tl.clips).id)
                else:
                    continue
                undo.append(execute(tl, op))
            except (TimelineError, IndexError, ValueError):
                continue
            tl.validate()
        for inv in reversed(undo):
            execute(tl, inv)
        end = tl.canonical()
        assert {**end, "retired": []} == {**start, "retired": []}


def test_default_durations_when_none_is_given():
    tl = tl3()
    execute(tl, add_transition(tl, "s001", "flash"))
    execute(tl, add_transition(tl, "s002", "fade"))
    assert [t.duration for t in tl.transitions] == [6, 12]
