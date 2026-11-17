"""Rognage d'un bord (moteur) : limites, voisins, poignées du rush, plans liés, une seule annulation."""

import pytest

from kolnoa_editor.timeline import Clip, Timeline, TimelineError, Track, trim_edge, trim_limits
from kolnoa_editor.timeline.model import LockedError
from kolnoa_editor.timeline.ops import execute

FPS = 25


def tl3():
    tl = Timeline(title="T", fps=FPS, tracks=[Track("V1", "video"), Track("A1", "audio")])
    tl.clips += [Clip("a", "V1", "r/a.mp4", 100, 50, src_in=2.0, link="L"),     # 100-150, source 2.0 s → 4.0 s
                 Clip("b", "V1", "r/a.mp4", 150, 50, src_in=10.0),              # contigu à a
                 Clip("c", "V1", "r/a.mp4", 260, 50, src_in=5.0),
                 Clip("s", "A1", "r/a.mp4", 100, 50, src_in=2.0, link="L")]     # son lié à a
    tl.validate()
    return tl


LEN = lambda media: 30.0


def test_limits_respect_neighbours_source_and_min_duration():
    tl = tl3()
    assert trim_limits(tl, "a", "tail", False, LEN) == (-49, 0)          # b est collé : on ne peut que raccourcir
    assert trim_limits(tl, "c", "head", False, LEN) == (-60, 49)         # jusqu'à la fin de b (200) ; 5 s de poignée source
    lo, hi = trim_limits(tl, "c", "tail", False, LEN)
    assert hi == int((30.0 - 5.0) * FPS) - 50 and lo == -49                      # le rush dure 30 s
    lo, hi = trim_limits(tl, "a", "head", False, LEN)
    assert lo == -round(2.0 * FPS) and hi == 49                           # 2 s de poignée avant le plan (et début du film)


def test_linked_clips_intersect_limits_and_move_together():
    tl = tl3()
    tl.clips.append(Clip("x", "A1", "r/a.mp4", 160, 20))                  # bloque la queue du son lié (a.s se termine à 150)
    lo, hi = trim_limits(tl, "a", "tail", True, LEN)
    assert hi == 0                                                        # b bloque déjà l'image, x ne change rien ici
    lo, hi = trim_limits(tl, "s", "tail", True, LEN)
    assert hi == 0
    tx = trim_edge(tl, "a", "head", 10, True, LEN)
    execute(tl, tx)
    assert (tl.clip("a").start, tl.clip("s").start) == (110, 110)
    assert tl.clip("a").src_in == pytest.approx(2.4) and tl.clip("s").src_in == pytest.approx(2.4)


def test_refusals_change_nothing_and_name_the_limit():
    tl = tl3()
    snap = tl.to_dict()
    for edge, d in (("tail", 5), ("head", -999), ("head", 50), ("tail", -50)):
        with pytest.raises(TimelineError):
            trim_edge(tl, "a", edge, d, True, LEN)
    with pytest.raises(TimelineError):
        trim_edge(tl, "a", "tail", 0)
    assert tl.to_dict() == snap


def test_locked_clip_or_track_refuses():
    tl = tl3()
    tl.clip("c").locked = True
    with pytest.raises(LockedError):
        trim_edge(tl, "c", "tail", -5)
    tl = tl3()
    tl.track("V1").locked = True
    with pytest.raises(LockedError):
        trim_limits(tl, "c", "head")


def test_fades_must_still_fit():
    tl = tl3()
    tl.clip("c").fade_in = 1.0
    tl.clip("c").fade_out = 1.0                                           # 2 s = 50 i : le plan ne peut pas raccourcir
    assert trim_limits(tl, "c", "tail", False, LEN)[0] == 0


def test_one_transaction_is_one_undo(tmp_path):
    from kolnoa_editor.timeline.session import Session
    s = Session.create(tmp_path / "s", tl3())
    s.do(trim_edge(s.tl, "a", "head", 10, True, LEN), "rognage")
    assert s.tl.clip("a").start == 110
    s.undo()
    assert (s.tl.clip("a").start, s.tl.clip("s").start, s.tl.clip("a").src_in) == (100, 100, 2.0)
    s.redo()
    assert s.tl.clip("a").start == 110


def test_group_clips_have_no_source_length_check_but_keep_neighbour_rule():
    tl = tl3()
    tl.clip("c").group = "g"   # un plan multicam ne dépend pas de la durée d'un seul rush
    assert trim_limits(tl, "c", "tail", False, lambda m: 1.0)[1] > 1000
