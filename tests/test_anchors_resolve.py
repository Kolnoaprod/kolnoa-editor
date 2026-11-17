"""A1-2 : ancres de contenu, résolveur à états typés, contrat ConfirmedTarget."""
import pytest

from kolnoa_editor.anchors import (AXIS_GROUP, AcceptFragments, AcceptNearestEdge, Acknowledge, AnchorRefused, ChooseOccurrence,
                                   ConfirmationRefused, ConfirmedTarget, MediaRegistry, Resolver, StaleTarget, anchor_at,
                                   check_fresh, range_between)
from kolnoa_editor.anchors.timebase import offset_to_us, rdiv, us_to_offset
from kolnoa_editor.timeline.editing import razor  # noqa: F401  (présence du builder)
from kolnoa_editor.timeline.model import Angle, Clip, MulticamGroup, Timeline, Track
from kolnoa_editor.timeline.ops import execute
from kolnoa_editor.timeline import editing


def make(clips, fps=25, groups=None, vfr=False, fps_media="25/1"):
    tl = Timeline(title="t", fps=fps, tracks=[Track("V1"), Track("V2")], clips=clips, groups=groups or [])
    reg = MediaRegistry()
    for m in sorted({c.media for c in clips} | {a.media for g in (groups or []) for a in g.angles}):
        reg.register(m, 1000 + len(m), 1, duration_us=60_000_000, fps=fps_media, vfr=vfr, quick_hash="h" + m)
    return tl, reg


def C(id, media="a.mov", start=0, dur=100, src_in=10.0, track="V1", **kw):
    return Clip(id=id, track=track, media=media, start=start, duration=dur, src_in=src_in, **kw)


def test_rdiv_and_roundtrip_exact():
    assert rdiv(1, 2) == 1 and rdiv(-1, 2) == 0 and rdiv(7, 3) == 2
    for fps in (24, 25, 30, 50, 60):
        assert all(us_to_offset(offset_to_us(f, fps), fps) == f for f in range(0, 20000))


def test_point_exact_and_survives_move():
    tl, reg = make([C("c1")])
    a = anchor_at(tl, reg, "V1", 40, "k1")
    assert Resolver(tl, reg).resolve(a).options[0][0].frame == 40
    tl.clips[0].start = 300                                   # le plan est déplacé : l'ancre suit le contenu
    r = Resolver(tl, reg).resolve(a)
    assert r.state == "exacte" and r.options[0][0].frame == 340


def test_point_survives_razor_and_ripple_delete():
    tl, reg = make([C("c1", dur=200)])
    a = anchor_at(tl, reg, "V1", 150, "k")
    execute(tl, editing.razor(tl, 100))
    r = Resolver(tl, reg).resolve(a)
    assert r.state == "exacte" and r.options[0][0].frame == 150 and len(tl.clips) == 2


def test_point_at_cut_belongs_to_right_clip_no_false_multiple():
    tl, reg = make([C("c1", dur=100), C("c2", start=100, dur=100, src_in=14.0)])   # 100 images à 25 i/s = 4,0 s : continu
    a = anchor_at(tl, reg, "V1", 100, "k")
    r = Resolver(tl, reg).resolve(a)
    assert r.state == "exacte" and r.options[0][0].clip_id == "c2"


def test_hors_plan_and_nearest_edge_only_on_explicit_accept():
    tl, reg = make([C("c1", dur=100)])
    a = anchor_at(tl, reg, "V1", 50, "k")
    tl.clips[0].duration = 25                                  # le contenu ancré est retiré
    r = Resolver(tl, reg).resolve(a)
    assert r.state == "hors_plan" and r.nearest is not None
    with pytest.raises(ConfirmationRefused):
        r.confirm()
    t = r.confirm(AcceptNearestEdge())
    assert t.via == "bord_proche" and t.segments[0].clip_id == "c1"


def test_multiple_requires_choice():
    tl, reg = make([C("c1", dur=100), C("c2", start=500, dur=100, track="V2")])    # même contenu utilisé deux fois
    a = anchor_at(tl, reg, "V1", 10, "k")
    r = Resolver(tl, reg).resolve(a)
    assert r.state == "multiple" and len(r.options) == 2 and "doublon" in r.flags
    for bad in (None, ChooseOccurrence(5), AcceptFragments()):
        with pytest.raises(ConfirmationRefused):
            r.confirm(bad)
    assert r.confirm(ChooseOccurrence(1)).via == "occurrence"


def test_perdue_when_media_missing_or_replaced_and_never_confirmable():
    tl, reg = make([C("c1")])
    a = anchor_at(tl, reg, "V1", 10, "k")
    reg.mark_missing(set())
    r = Resolver(tl, reg).resolve(a)
    assert r.state == "perdue"
    for ch in (None, Acknowledge(), AcceptFragments(), AcceptNearestEdge(), ChooseOccurrence(0)):
        with pytest.raises(ConfirmationRefused):
            r.confirm(ch)


def test_relink_keeps_anchor_valid():
    tl, reg = make([C("c1")])
    a = anchor_at(tl, reg, "V1", 10, "k")
    mid = a.key
    reg.mark_missing(set())
    reg.confirm_relink(mid, "new/a.mov")
    tl.clips[0].media = "new/a.mov"
    assert Resolver(tl, reg).resolve(a).state == "exacte"


@pytest.mark.parametrize("kw", [dict(vfr=True), dict(fps_media="30000/1001")])
def test_unsupported_vfr_and_fractional_fps_refused(kw):
    tl, reg = make([C("c1")], **kw)
    with pytest.raises(AnchorRefused):
        anchor_at(tl, reg, "V1", 10, "k")


def test_unsupported_speed_refused_and_state_when_changed_later():
    tl, reg = make([C("c1")])
    a = anchor_at(tl, reg, "V1", 10, "k")
    tl.clips[0].extra["speed"] = 2.0
    r = Resolver(tl, reg).resolve(a)
    assert r.state == "non_supportée"
    with pytest.raises(ConfirmationRefused):
        r.confirm()
    tl.clips[0].extra["speed"] = 0.5
    with pytest.raises(AnchorRefused):
        anchor_at(tl, reg, "V1", 10, "k2")


def test_50p_source_on_25_timeline_supported():
    tl, reg = make([C("c1")], fps=25, fps_media="50/1")
    a = anchor_at(tl, reg, "V1", 33, "k")
    assert Resolver(tl, reg).resolve(a).options[0][0].frame == 33


def test_range_exact_through_razor_pieces_merged_as_one_chain():
    tl, reg = make([C("c1", dur=200)])
    rg = range_between(tl, reg, "V1", 20, 180, "r")
    execute(tl, editing.razor(tl, 100))
    r = Resolver(tl, reg).resolve(rg)
    assert r.state == "exacte" and len(r.options) == 1 and len(r.options[0]) == 2
    assert (r.options[0][0].frame, r.options[0][-1].frame_end) == (20, 180)


def test_range_fragmented_after_removing_middle():
    tl, reg = make([C("c1", dur=100), C("c2", start=100, dur=100, src_in=14.0)])
    rg = range_between(tl, reg, "V1", 10, 190, "r")
    tl.clips[1].src_in = 15.0                                 # coupe un trou d'une seconde dans la source
    r = Resolver(tl, reg).resolve(rg)
    assert r.state == "fragmentée" and r.missing_us > 0
    with pytest.raises(ConfirmationRefused):
        r.confirm()
    t = r.confirm(AcceptFragments())
    assert t.via == "fragments" and len(t.segments) == 2


def test_range_trimmed_edge_is_fragmented_not_exact():
    tl, reg = make([C("c1", dur=100)])
    rg = range_between(tl, reg, "V1", 0, 100, "r")
    tl.clips[0].duration = 60
    assert Resolver(tl, reg).resolve(rg).state == "fragmentée"


def test_range_duplicate_is_multiple():
    tl, reg = make([C("c1", dur=100), C("c2", start=300, dur=100, track="V2")])
    rg = range_between(tl, reg, "V1", 0, 100, "r")
    assert Resolver(tl, reg).resolve(rg).state == "multiple"


def test_range_refused_across_sources_or_holes():
    tl, reg = make([C("c1", dur=50), C("c2", media="b.mov", start=50, dur=50)])
    with pytest.raises(AnchorRefused):
        range_between(tl, reg, "V1", 10, 90, "r")
    tl, reg = make([C("c1", dur=50), C("c2", start=60, dur=50, src_in=12.4)])
    with pytest.raises(AnchorRefused):
        range_between(tl, reg, "V1", 10, 100, "r")
    tl, reg = make([C("c1", dur=50), C("c2", start=50, dur=50, src_in=99.0)])
    with pytest.raises(AnchorRefused):
        range_between(tl, reg, "V1", 10, 90, "r")


def test_context_modified_needs_acknowledge():
    tl, reg = make([C("c1", dur=50), C("c2", media="b.mov", start=50, dur=50)])
    a = anchor_at(tl, reg, "V1", 10, "k")
    tl.clips[1].media = "c.mov"
    reg.register("c.mov", 5, 1, duration_us=1, fps="25/1")
    r = Resolver(tl, reg).resolve(a)
    assert r.state == "contexte_modifié"
    with pytest.raises(ConfirmationRefused):
        r.confirm()
    assert r.confirm(Acknowledge()).via == "contexte"


def test_confirmed_target_cannot_be_forged_or_reused_stale():
    tl, reg = make([C("c1")])
    res = Resolver(tl, reg)
    r = res.resolve(anchor_at(tl, reg, "V1", 10, "k"))
    with pytest.raises(TypeError):
        ConfirmedTarget("x", "k", (), "exacte", "exacte", "t")
    with pytest.raises(ConfirmationRefused):
        r.confirm(Acknowledge())                               # un choix superflu est refusé aussi
    t = r.confirm()
    check_fresh(t, res)
    tl.clips[0].start += 5
    with pytest.raises(StaleTarget):
        check_fresh(t, res)


def test_multicam_group_axis_follows_angle_switch_and_offset():
    g = MulticamGroup("g1", "A", angles=[Angle("A", "a.mov", offset=0.0), Angle("B", "b.mov", offset=2.0, status="synced")])
    tl, reg = make([Clip("m1", "V1", "a.mov", 0, 100, src_in=10.0, group="g1", angle="A")], groups=[g])
    a = anchor_at(tl, reg, "V1", 25, "k", axis=AXIS_GROUP)         # t_groupe = 11 s
    assert a.us == 11_000_000 and a.key == "g1"
    c = tl.clips[0]
    c.media, c.angle, c.src_in = "b.mov", "B", g.angle("B").to_angle(10.0)   # même instant de groupe, autre caméra
    r = Resolver(tl, reg).resolve(a)
    assert r.state == "exacte" and r.options[0][0].frame == 25


def test_multicam_drift_within_one_frame():
    g = MulticamGroup("g1", "A", angles=[Angle("A", "a.mov", offset=1.0, drift_ppm=120.0)])
    tl, reg = make([Clip("m1", "V1", "a.mov", 100, 500, src_in=30.0, group="g1", angle="A")], groups=[g])
    for f in (100, 333, 599):
        a = anchor_at(tl, reg, "V1", f, f"k{f}", axis=AXIS_GROUP)
        assert Resolver(tl, reg).resolve(a).options[0][0].frame == f


def test_unknown_group_is_perdue():
    g = MulticamGroup("g1", "A", angles=[Angle("A", "a.mov")])
    tl, reg = make([Clip("m1", "V1", "a.mov", 0, 100, src_in=1.0, group="g1", angle="A")], groups=[g])
    a = anchor_at(tl, reg, "V1", 10, "k", axis=AXIS_GROUP)
    tl.groups.clear()
    tl.clips[0].group = tl.clips[0].angle = None
    assert Resolver(tl, reg).resolve(a).state == "perdue"
