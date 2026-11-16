"""PR C2a : moteur de montage (modes, rasoir, liens, ripple, roll, aimantation) : tout est réversible et atomique."""

import copy

import pytest

from kolnoa_editor.decision_list import DecisionList, Segment
from kolnoa_editor.timeline import (AddTrack, Clip, LockedError, Marker, SetClipProps, Timeline, TimelineError, execute,
                                    from_decision_list, delete_range, link_clips, linked_ids, move_clips, place_clip,
                                    razor, replace_clip, ripple_trim, roll_trim, snap_frame, snap_move, snap_points,
                                    unlink_clips, op_from_dict)
from kolnoa_editor.timeline.ops import AddMarker

SONY = "MAIRIE/A7IV/20261011_C4837.MP4"
OSMO = "MAIRIE/OP4/DJI_20261011100000_0001_D.MP4"
FPS = 25


def base():
    """V1 : s001 [0,100) s002 [100,200) ; A1 : s003 [0,200) (son lié à V1) ; V2 libre (plans de coupe)."""
    tl = from_decision_list(DecisionList(title="T", resolution=(640, 360),
                                         segments=[Segment("s001", SONY, 0, 4), Segment("s002", SONY, 10, 14)]))
    execute(tl, AddTrack({"id": "A1", "kind": "audio", "role": "voix"}))
    execute(tl, AddTrack({"id": "V2", "kind": "video", "role": "coupe"}))
    tl.clips.append(Clip("s003", "A1", "AUDIO/rec.wav", 0, 200, 5.0, audio="source"))
    tl.validate()
    return tl


def clips(tl, track):
    return [(c.id, c.start, c.end, c.src_in) for c in tl.clips_on(track)]


def _core(tl):
    d = tl.canonical()
    d.pop("retired")                       # les ids supprimés ne sont jamais réutilisés (règle du modèle)
    return d


def roundtrip(tl, op):
    """Sur une COPIE : annule exactement, rétablit exactement, et rejoue la forme journalisée sur une autre copie."""
    work, fresh = tl.copy(), tl.copy()
    before = _core(work)
    inv = execute(work, op)
    after = work.canonical()
    redo = execute(work, inv)
    assert _core(work) == before
    execute(work, redo)
    assert work.canonical() == after
    execute(fresh, op_from_dict(op.to_dict()))
    assert fresh.canonical() == after
    return tl


# --- liens ---------------------------------------------------------------------------------------
def test_link_and_unlink_pair_image_and_sound():
    tl = base()
    execute(tl, link_clips(tl, ["s001", "s003"]))
    assert linked_ids(tl, "s001") == ["s001", "s003"] and linked_ids(tl, "s002") == ["s002"]
    with pytest.raises(TimelineError):
        link_clips(tl, ["s001"])
    execute(tl, unlink_clips(tl, "s001"))
    assert linked_ids(tl, "s001") == ["s001"] and tl.clip("s003").link is None


def test_two_clips_on_the_same_track_cannot_be_linked():
    tl = base()
    with pytest.raises(TimelineError):
        link_clips(tl, ["s001", "s002"])


# --- rasoir ---------------------------------------------------------------------------------------
def test_razor_cuts_image_and_sound_together_and_relinks_the_right_halves():
    tl = base()
    execute(tl, link_clips(tl, ["s001", "s003"]))
    op = razor(tl, 50)
    roundtrip(tl, op)
    execute(tl, op)
    assert clips(tl, "V1")[0][:3] == ("s001", 0, 50) and clips(tl, "V1")[1][1:3] == (50, 100)
    right_v = tl.clip(clips(tl, "V1")[1][0])
    right_a = [c for c in tl.clips_on("A1") if c.start == 50][0]
    assert right_v.link == right_a.link and right_v.link not in (None, tl.clip("s001").link)   # moitiés droites liées entre elles
    assert tl.clip("s001").link == tl.clip("s003").link                                         # moitiés gauches restent liées
    assert right_a.src_in == pytest.approx(5.0 + 50 / FPS)                                      # son coupé au même instant


def test_razor_unlinked_option_cuts_only_the_clip_under_the_cursor_track():
    tl = base()
    execute(tl, link_clips(tl, ["s001", "s003"]))
    execute(tl, razor(tl, 50, tracks=["V1"], linked=False))
    assert len(tl.clips_on("V1")) == 3 and len(tl.clips_on("A1")) == 1


def test_razor_on_all_tracks_skips_locked_tracks_but_a_named_locked_track_is_refused():
    tl = base()
    execute(tl, __import__("kolnoa_editor.timeline", fromlist=["SetTrackProps"]).SetTrackProps("A1", {"locked": True}))
    execute(tl, razor(tl, 50))
    assert len(tl.clips_on("V1")) == 3 and len(tl.clips_on("A1")) == 1
    with pytest.raises(LockedError):
        razor(tl, 150, tracks=["A1"])


def test_razor_refuses_when_nothing_to_cut_and_never_cuts_on_an_edge():
    tl = base()
    with pytest.raises(TimelineError, match="rien à couper"):
        razor(tl, 100, tracks=["V1"])                    # pile sur la coupe existante
    with pytest.raises(TimelineError):
        razor(tl, 5000)


def test_razor_on_a_linked_clip_whose_partner_track_is_locked_is_refused_atomically():
    tl = base()
    execute(tl, link_clips(tl, ["s001", "s003"]))
    from kolnoa_editor.timeline import SetTrackProps
    execute(tl, SetTrackProps("A1", {"locked": True}))
    snap = tl.canonical()
    with pytest.raises(LockedError):
        razor(tl, 50, tracks=["V1"])
    assert tl.canonical() == snap


# --- recouvrement (overwrite) ------------------------------------------------------------------------
def test_overwrite_in_the_middle_splits_the_existing_clip_and_keeps_its_source_continuity():
    tl = base()
    new = Clip("s010", "V1", OSMO, 40, 20, 3.0)
    roundtrip(tl, place_clip(tl, new, "overwrite"))
    execute(tl, place_clip(tl, new, "overwrite"))
    v1 = clips(tl, "V1")
    assert [(a, b) for _, a, b, _ in v1] == [(0, 40), (40, 60), (60, 100), (100, 200)]
    assert v1[2][3] == pytest.approx(0 + 60 / FPS)                 # la fin de s001 reprend à la bonne image source
    assert tl.clip("s002").start == 100                             # rien d'autre n'a bougé


def test_overwrite_trims_both_neighbours_and_removes_covered_clips():
    tl = base()
    execute(tl, razor(tl, 150, tracks=["V1"]))                      # s002 -> [100,150) + [150,200)
    execute(tl, place_clip(tl, Clip("s020", "V1", OSMO, 90, 80, 0.0), "overwrite"))   # recouvre [90,170)
    v1 = clips(tl, "V1")
    assert [(a, b) for _, a, b, _ in v1] == [(0, 90), (90, 170), (170, 200)]
    assert v1[2][3] == pytest.approx(10.0 + 50 / FPS + 20 / FPS)      # 12.8 s : même instant source qu'avant la coupe
    assert tl.clip(v1[2][0]).fade_in == 0.0 and tl.clip(v1[0][0]).fade_out == 0.0   # bords coupés sans fondu


def test_overwrite_on_a_free_track_changes_nothing_else():
    tl = base()
    snap = {c.id: (c.start, c.duration, c.src_in) for c in tl.clips}
    execute(tl, place_clip(tl, Clip("s030", "V2", OSMO, 60, 40, 1.0), "overwrite"))
    assert {c.id: (c.start, c.duration, c.src_in) for c in tl.clips if c.id != "s030"} == snap   # V1 et A1 intacts


def test_overwrite_refuses_locked_clips_and_leaves_everything_untouched():
    tl = base()
    execute(tl, SetClipProps("s001", {"locked": True}))
    snap = tl.canonical()
    with pytest.raises(LockedError):
        execute(tl, place_clip(tl, Clip("s040", "V1", OSMO, 30, 10, 0.0), "overwrite"))
    assert tl.canonical() == snap


# --- insertion ----------------------------------------------------------------------------------------
def test_insert_shifts_only_the_target_track_by_default():
    tl = base()
    roundtrip(tl, place_clip(tl, Clip("s050", "V1", OSMO, 100, 30, 0.0), "insert"))
    execute(tl, place_clip(tl, Clip("s050", "V1", OSMO, 100, 30, 0.0), "insert"))
    assert [(a, b) for _, a, b, _ in clips(tl, "V1")] == [(0, 100), (100, 130), (130, 230)]
    assert clips(tl, "A1") == [("s003", 0, 200, 5.0)]               # le son n'a pas bougé (comme demandé : piste visée seule)


def test_insert_in_the_middle_splits_and_can_ripple_other_tracks_to_stay_in_sync():
    tl = base()
    execute(tl, place_clip(tl, Clip("s060", "V1", OSMO, 50, 25, 0.0), "insert", ripple_tracks=["A1"]))
    assert [(a, b) for _, a, b, _ in clips(tl, "V1")] == [(0, 50), (50, 75), (75, 125), (125, 225)]
    assert [(a, b) for _, a, b, _ in clips(tl, "A1")] == [(0, 50), (75, 225)]   # le son est coupé et décalé aussi
    tl.validate()


def test_insert_refuses_to_desync_a_locked_track_with_clips_after():
    tl = base()
    from kolnoa_editor.timeline import SetTrackProps
    execute(tl, SetTrackProps("A1", {"locked": True}))
    with pytest.raises(LockedError, match="désynchronis"):
        place_clip(tl, Clip("s070", "V1", OSMO, 50, 25, 0.0), "insert")


def test_unknown_mode_is_refused():
    with pytest.raises(TimelineError):
        place_clip(base(), Clip("s080", "V1", OSMO, 0, 10, 0.0), "magie")


# --- déplacement -----------------------------------------------------------------------------------------
def test_moving_a_cutaway_to_another_track_moves_nothing_else():
    tl = base()
    execute(tl, place_clip(tl, Clip("s090", "V2", OSMO, 10, 30, 0.0), "overwrite"))
    snap = {c.id: (c.track, c.start) for c in tl.clips if c.id != "s090"}
    execute(tl, move_clips(tl, "s090", 120))
    assert (tl.clip("s090").track, tl.clip("s090").start) == ("V2", 120)
    assert {c.id: (c.track, c.start) for c in tl.clips if c.id != "s090"} == snap


def test_default_move_refuses_an_overlap_and_overwrite_mode_covers():
    tl = base()
    execute(tl, place_clip(tl, Clip("s091", "V2", OSMO, 0, 30, 0.0), "overwrite"))
    with pytest.raises(TimelineError):
        execute(tl, move_clips(tl, "s091", 20, track="V1"))               # chevauche s001 : refus, état intact
    assert tl.clip("s091").track == "V2"
    execute(tl, move_clips(tl, "s091", 20, track="V1", mode="overwrite"))
    assert [(a, b) for _, a, b, _ in clips(tl, "V1")] == [(0, 20), (20, 50), (50, 100), (100, 200)]


def test_move_linked_pair_offsets_the_partner_by_the_same_delta():
    tl = base()
    tl.clips = [c for c in tl.clips if c.id != "s003"]
    tl.clips.append(Clip("s003", "A1", "AUDIO/rec.wav", 0, 100, 5.0))
    execute(tl, link_clips(tl, ["s001", "s003"]))
    execute(tl, move_clips(tl, "s001", 300))
    assert tl.clip("s001").start == 300 and tl.clip("s003").start == 300
    execute(tl, move_clips(tl, "s001", 250, linked=False))
    assert tl.clip("s001").start == 250 and tl.clip("s003").start == 300      # dissocié : seul le plan visé bouge


# --- remplacement ----------------------------------------------------------------------------------------
def test_replace_keeps_position_and_duration_and_refuses_multicam():
    tl = base()
    roundtrip(tl, replace_clip(tl, "s002", OSMO, 7.5))
    execute(tl, replace_clip(tl, "s002", OSMO, 7.5))
    c = tl.clip("s002")
    assert (c.start, c.duration, c.media, c.src_in) == (100, 100, OSMO, 7.5)
    with pytest.raises(TimelineError):
        replace_clip(tl, "s002", OSMO, -1)


# --- suppression de plage ------------------------------------------------------------------------------
def test_delete_range_without_ripple_leaves_a_gap_and_touches_all_tracks():
    tl = base()
    roundtrip(tl, delete_range(tl, 50, 150))
    execute(tl, delete_range(tl, 50, 150))
    assert [(a, b) for _, a, b, _ in clips(tl, "V1")] == [(0, 50), (150, 200)]
    assert [(a, b) for _, a, b, _ in clips(tl, "A1")] == [(0, 50), (150, 200)]


def test_delete_range_with_ripple_closes_the_gap_and_keeps_sync():
    tl = base()
    roundtrip(tl, delete_range(tl, 50, 150, ripple=True))
    execute(tl, delete_range(tl, 50, 150, ripple=True))
    assert [(a, b) for _, a, b, _ in clips(tl, "V1")] == [(0, 50), (50, 100)]
    assert [(a, b) for _, a, b, _ in clips(tl, "A1")] == [(0, 50), (50, 100)]
    v, a = clips(tl, "V1")[1], clips(tl, "A1")[1]
    assert v[3] == pytest.approx(10.0 + 50 / FPS) and a[3] == pytest.approx(5.0 + 150 / FPS)   # même instant source qu'avant
    assert tl.end_frame == 100


def test_ripple_delete_refuses_to_desync_a_locked_track_with_clips_after():
    tl = base()
    from kolnoa_editor.timeline import SetTrackProps
    execute(tl, SetTrackProps("V2", {"locked": True}))
    tl.clips.append(Clip("s100", "V2", OSMO, 180, 10, 0.0))
    snap = tl.canonical()
    with pytest.raises(LockedError, match="désynchronis"):
        delete_range(tl, 50, 150, ripple=True)
    assert tl.canonical() == snap


def test_delete_range_on_chosen_tracks_pulls_in_linked_partner_tracks():
    tl = base()
    execute(tl, link_clips(tl, ["s001", "s003"]))
    execute(tl, delete_range(tl, 10, 30, tracks=["V1"]))
    assert [(a, b) for _, a, b, _ in clips(tl, "A1")] == [(0, 10), (30, 200)]
    tl2 = base()
    execute(tl2, link_clips(tl2, ["s001", "s003"]))
    execute(tl2, delete_range(tl2, 10, 30, tracks=["V1"], linked=False))
    assert [(a, b) for _, a, b, _ in clips(tl2, "A1")] == [(0, 200)]


def test_delete_range_errors():
    tl = base()
    for args in ((50, 50), (-1, 10), (9000, 9100)):
        with pytest.raises(TimelineError):
            delete_range(tl, *args)


# --- ripple trim et roll ---------------------------------------------------------------------------------
def test_ripple_trim_tail_shifts_the_rest_of_the_track():
    tl = base()
    roundtrip(tl, ripple_trim(tl, "s001", "tail", 20))
    execute(tl, ripple_trim(tl, "s001", "tail", 20))
    assert [(a, b) for _, a, b, _ in clips(tl, "V1")] == [(0, 80), (80, 180)]
    execute(tl, ripple_trim(tl, "s001", "tail", -10))                       # rallonger
    assert [(a, b) for _, a, b, _ in clips(tl, "V1")] == [(0, 90), (90, 190)]


def test_ripple_trim_head_keeps_the_start_and_moves_the_source_in():
    tl = base()
    execute(tl, ripple_trim(tl, "s002", "head", 25))
    c = tl.clip("s002")
    assert (c.start, c.duration) == (100, 75) and c.src_in == pytest.approx(10.0 + 1.0)


def test_trim_checks_source_handles():
    tl = base()
    with pytest.raises(TimelineError, match="poignée"):
        ripple_trim(tl, "s001", "head", -5)                                 # src_in 0 : rien avant
    length = {SONY: 14.0}.get
    with pytest.raises(TimelineError, match="se termine"):
        ripple_trim(tl, "s002", "tail", -50, source_len=length)             # 10 s + 6 s > 14 s


def test_roll_trim_moves_the_cut_without_changing_total_duration():
    tl = base()
    roundtrip(tl, roll_trim(tl, "s001", "s002", 20))
    execute(tl, roll_trim(tl, "s001", "s002", 20))
    assert [(a, b) for _, a, b, _ in clips(tl, "V1")] == [(0, 120), (120, 200)]
    assert tl.clip("s002").src_in == pytest.approx(10.0 + 20 / FPS) and tl.end_frame == 200
    execute(tl, roll_trim(tl, "s001", "s002", -40))
    assert tl.clip("s002").src_in == pytest.approx(10.0 - 20 / FPS)
    with pytest.raises(TimelineError):
        roll_trim(tl, "s001", "s002", 5000)
    with pytest.raises(TimelineError):
        roll_trim(tl, "s001", "s003", 5)


# --- aimantation -----------------------------------------------------------------------------------------
def test_snap_points_and_frame():
    tl = base()
    execute(tl, AddMarker(Marker("m001", 77, "note", "x").__dict__))
    pts = snap_points(tl, playhead=33)
    assert {0, 100, 200, 77, 33} <= set(pts) and pts == sorted(pts)
    assert snap_frame(98, pts, 3) == 100 and snap_frame(90, pts, 3) == 90
    assert snap_frame(75, pts, 4) == 77
    assert snap_points(tl, exclude={"s001", "s003"}, tracks=["V1"]) == sorted({0, 77, 100, 200})


def test_snap_move_uses_either_edge_and_ignores_the_clips_own_edges_and_partners():
    tl = base()
    execute(tl, place_clip(tl, Clip("s110", "V2", OSMO, 10, 30, 0.0), "overwrite"))
    assert snap_move(tl, "s110", 97, 4) == 100                                 # début aimanté sur la coupe 100
    assert snap_move(tl, "s110", 72, 4) == 70                                  # la fin (72+30=102) vient sur 100
    assert snap_move(tl, "s110", 60, 3) == 60                                  # rien d'assez proche
    execute(tl, link_clips(tl, ["s110", "s003"]))
    assert snap_move(tl, "s110", 12, 3) == 12                                  # ne s'aimante pas sur son lié (0)


# --- scénarios de montage et persistance -----------------------------------------------------------------
def test_scenario_insert_a_cutaway_then_compensate_the_next_shot():
    """Intercaler un plan de 30 i sur V1 (la suite recule), puis raccourcir le début du plan suivant de 30 i :
    le montage retrouve sa durée et le plan suivant reprend au bon endroit de sa source."""
    tl = base()
    execute(tl, place_clip(tl, Clip("s120", "V1", OSMO, 100, 30, 0.0), "insert"))
    assert tl.end_frame == 230 and tl.clip("s002").start == 130
    execute(tl, ripple_trim(tl, "s002", "head", 30))
    c = tl.clip("s002")
    assert (c.start, c.duration, tl.end_frame) == (130, 70, 200)
    assert c.src_in == pytest.approx(10.0 + 30 / FPS)


def test_editing_sessions_survive_close_undo_redo_and_reopen(tmp_path):
    from kolnoa_editor.timeline import Session

    d = tmp_path / "tl"
    s = Session.create(d, base())
    s.do(razor(s.tl, 50), "rasoir")
    s.do(delete_range(s.tl, 20, 60, ripple=True), "retire")
    snap = s.tl.canonical()
    s.undo()
    s.undo()
    s.redo()
    s.redo()
    assert s.tl.canonical() == snap
    reopened = Session.open(d)
    assert reopened.tl.canonical() == snap
    reopened.undo()
    reopened.undo()
    assert [(a, b) for _, a, b, _ in clips(reopened.tl, "V1")] == [(0, 100), (100, 200)]
