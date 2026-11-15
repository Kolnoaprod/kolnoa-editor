"""Timeline canonique : modèle, opérations réversibles, journal, passerelle liste de décisions."""

import json
import random

import pytest

from kolnoa_editor.decision_list import DecisionList, Music, Segment
from kolnoa_editor.timeline import (
    AddClip, AddMarker, AddTrack, LockedError, MoveClip, RemoveClip, Session, SetClipProps,
    SetProject, SetTrackProps, ShiftClips, SplitClip, Timeline, TimelineError, TimelineExportError,
    Transaction, TrimClip, execute, from_decision_list, op_from_dict, ripple_delete, to_decision_list,
    trim_head, trim_tail)
from kolnoa_editor.timeline.model import migrate

SONY = "MAIRIE/A7IV/20261011_C4837.MP4"
CANON = "SOIREE/CANON/A_0009C238A261108_113335HL_CANON.MP4"


def dl3():
    return DecisionList(title="T", version=2, resolution=(640, 360), music=Music("MUSIQUE/x.mp3", 1.0, -3.0, 2.0),
                        retired=["s009"], segments=[
        Segment("s001", SONY, 0.0, 2.0, fade_in=0.2, fade_out=0.4, note="ouverture"),
        Segment("s002", CANON, 1.0, 3.0, audio="mute"),
        Segment("s003", SONY, 4.0, 5.0, parent="s001")])


def tl3():
    return from_decision_list(dl3())


# --- passerelle -------------------------------------------------------------
def test_import_lays_clips_end_to_end_in_whole_frames():
    tl = tl3()
    assert [(c.id, c.start, c.duration) for c in tl.clips] == [("s001", 0, 50), ("s002", 50, 50), ("s003", 100, 25)]
    assert tl.end_frame == 125 and tl.duration == 5.0
    assert all(isinstance(c.start, int) and isinstance(c.duration, int) for c in tl.clips)
    assert tl.retired == ["s009"] and tl.clips[2].parent == "s001"


def test_roundtrip_decision_list_is_lossless_for_frame_aligned_edits():
    dl = dl3()
    back, warnings = to_decision_list(from_decision_list(dl))
    assert back.to_dict() == dl.to_dict()
    assert warnings == []


def test_roundtrip_is_idempotent_for_off_grid_durations():
    dl = DecisionList(title="T", resolution=(640, 360), segments=[Segment("s001", SONY, 0.0, 1.013)])
    once, _ = to_decision_list(from_decision_list(dl))
    twice, _ = to_decision_list(from_decision_list(once))
    assert once.to_dict() == twice.to_dict()
    assert round(once.segments[0].duration * 25) == round(1.013 * 25)   # même nombre d'images au rendu


def test_export_refuses_what_it_cannot_represent():
    tl = tl3()
    execute(tl, MoveClip("s003", 110))                      # trou entre s002 et s003
    with pytest.raises(TimelineExportError, match="trou avant s003"):
        to_decision_list(tl)
    tl = tl3()
    execute(tl, AddTrack({"id": "V2", "kind": "video"}))
    execute(tl, AddClip({"id": "s010", "track": "V2", "media": SONY, "start": 0, "duration": 10}))
    with pytest.raises(TimelineExportError, match="2 pistes vidéo"):
        to_decision_list(tl)


def test_export_warns_about_dropped_markers_and_effects():
    tl = tl3()
    execute(tl, AddMarker({"id": "m001", "frame": 10, "kind": "beat"}))
    execute(tl, SetClipProps("s001", {"extra": {"grade": {"exposure": 0.3}}, "gain_db": -2.0}))
    _, warnings = to_decision_list(tl)
    assert any("repère" in w for w in warnings) and any("s001" in w for w in warnings)


# --- opérations et inverses --------------------------------------------------
def test_split_creates_child_with_lineage_and_keeps_duration():
    tl = tl3()
    inv = execute(tl, SplitClip("s002", 70))
    left, right = tl.clip("s002"), tl.clip("s010")          # s009 est retiré : jamais réutilisé
    assert (left.duration, right.start, right.duration) == (20, 70, 30)
    assert right.src_in == pytest.approx(1.0 + 20 / 25) and right.parent == "s002"
    assert tl.end_frame == 125
    execute(tl, inv)
    assert tl.canonical() == tl3().canonical() | {"retired": sorted(["s009", "s010"])}


def test_split_rejects_edges_and_keeps_fades_sane():
    tl = tl3()
    for at in (0, 50, 100):
        with pytest.raises(TimelineError):
            execute(tl, SplitClip("s001", at))
    execute(tl, SplitClip("s001", 25))
    assert tl.clip("s001").fade_out == 0.0 and tl.clip("s010").fade_in == 0.0 and tl.clip("s010").fade_out == 0.4


def test_remove_then_undo_restores_the_exact_clip_and_id():
    tl = tl3()
    before = tl.canonical()
    inv = execute(tl, RemoveClip("s002"))
    assert "s002" in tl.retired and len(tl.clips) == 2
    execute(tl, inv)
    assert tl.canonical() == before


def test_removed_ids_are_never_reused():
    tl = tl3()
    execute(tl, RemoveClip("s003"))
    with pytest.raises(TimelineError):
        execute(tl, AddClip({"id": "s003", "track": "V1", "media": SONY, "start": 100, "duration": 5}))
    assert tl.next_clip_id() not in {"s001", "s002", "s003", "s009"}


def test_ripple_delete_closes_the_gap_and_undoes_in_one_step():
    tl = tl3()
    before = tl.canonical()
    inv = execute(tl, ripple_delete(tl, "s001"))
    assert [(c.id, c.start) for c in tl.clips_on("V1")] == [("s002", 0), ("s003", 50)]
    assert isinstance(inv, Transaction)
    execute(tl, inv)
    assert tl.canonical() == before


def test_trim_helpers_keep_the_other_edge_in_place():
    tl = tl3()
    execute(tl, trim_head(tl, "s002", 10))                  # le début avance de 10 images, la fin reste
    c = tl.clip("s002")
    assert (c.start, c.end, c.src_in) == (60, 100, pytest.approx(1.4))
    execute(tl, trim_tail(tl, "s002", 15))
    assert tl.clip("s002").end == 85


def test_trim_cannot_make_fades_longer_than_the_clip():
    tl = tl3()
    with pytest.raises(TimelineError):
        execute(tl, TrimClip("s001", 0, 5, 0.0))            # 0,2 s + 0,4 s de fondus > 0,2 s
    assert tl.clip("s001").duration == 50                   # état intact après refus


def test_overlap_is_rejected_and_state_rolled_back():
    tl = tl3()
    before = tl.canonical()
    with pytest.raises(TimelineError, match="chevauchement"):
        execute(tl, MoveClip("s003", 90))
    assert tl.canonical() == before


def test_locks_protect_clips_and_tracks():
    tl = tl3()
    execute(tl, SetClipProps("s002", {"locked": True}))
    for op in (RemoveClip("s002"), MoveClip("s002", 60), TrimClip("s002", 50, 10, 1.0),
               SplitClip("s002", 60), SetClipProps("s002", {"note": "x"})):
        with pytest.raises(LockedError):
            execute(tl, op)
    # ripple_delete d'un plan avant un plan verrouillé : tout est refusé, rien ne bouge
    before = tl.canonical()
    with pytest.raises(LockedError):
        execute(tl, ripple_delete(tl, "s001"))
    assert tl.canonical() == before
    execute(tl, SetClipProps("s002", {"locked": False}))    # on peut toujours déverrouiller
    execute(tl, SetTrackProps("V1", {"locked": True}))
    with pytest.raises(LockedError):
        execute(tl, RemoveClip("s001"))


def test_transaction_is_all_or_nothing():
    tl = tl3()
    before = tl.canonical()
    tx = Transaction([RemoveClip("s001"), RemoveClip("s002"), RemoveClip("inconnu")])
    with pytest.raises(TimelineError):
        execute(tl, tx)
    assert tl.canonical() == before


def test_set_project_and_markers_roundtrip():
    tl = tl3()
    before = tl.canonical()
    i1 = execute(tl, SetProject({"title": "Autre", "music": None}))
    i2 = execute(tl, AddMarker({"id": "m001", "frame": 40, "kind": "keep", "text": "vœux"}))
    assert tl.title == "Autre" and tl.music is None and tl.marker("m001").kind == "keep"
    execute(tl, i2)
    execute(tl, i1)
    assert tl.canonical() == before


def test_invalid_properties_are_refused():
    tl = tl3()
    with pytest.raises(TimelineError):
        execute(tl, SetClipProps("s001", {"audio": "bruyant"}))
    with pytest.raises(TimelineError):
        execute(tl, SetClipProps("s001", {"start": 3}))      # la position se change par MoveClip
    with pytest.raises(TimelineError):
        execute(tl, SetProject({"fps": 30}))
    assert tl.clip("s001").audio == "source"


def test_ops_serialize_and_rebuild():
    ops = [SplitClip("s001", 25), ShiftClips(["s002"], 0), SetClipProps("s003", {"note": "é"}),
           Transaction([RemoveClip("s003")], "x"), AddTrack({"id": "A1", "kind": "audio", "role": "ambiance"})]
    for op in ops:
        again = op_from_dict(json.loads(json.dumps(op.to_dict())))
        assert again.to_dict() == op.to_dict()
    with pytest.raises(TimelineError):
        op_from_dict({"type": "explose"})


def test_random_operations_always_undo_to_the_start():
    """Propriété : n'importe quelle suite d'opérations valides, annulée à l'envers, rend l'état initial."""
    rng = random.Random(7)
    for _ in range(40):
        tl = tl3()
        start = tl.canonical()
        undo = []
        for _ in range(rng.randint(3, 12)):
            clips = tl.clips_on("V1")
            if not clips:
                break
            c = rng.choice(clips)
            choice = rng.choice(["split", "remove", "ripple", "trim_tail", "trim_head", "fade", "mute"])
            try:
                if choice == "split":
                    op = SplitClip(c.id, rng.randint(c.start + 1, c.end - 1))
                elif choice == "remove":
                    op = RemoveClip(c.id)
                elif choice == "ripple":
                    op = ripple_delete(tl, c.id)
                elif choice == "trim_tail":
                    op = trim_tail(tl, c.id, rng.randint(0, 10))
                elif choice == "trim_head":
                    op = trim_head(tl, c.id, rng.randint(0, 10))
                elif choice == "fade":
                    op = SetClipProps(c.id, {"fade_in": rng.choice([0.0, 0.1, 0.2])})
                else:
                    op = SetClipProps(c.id, {"audio": rng.choice(["source", "mute"])})
                undo.append(execute(tl, op))
            except (TimelineError, IndexError, ValueError):
                continue
            tl.validate()
        for inv in reversed(undo):
            execute(tl, inv)
        # tout est rendu à l'identique ; seuls les ids de scissions annulées restent « brûlés » (jamais réutilisés)
        end = tl.canonical()
        assert set(start["retired"]) <= set(end["retired"])
        assert {**end, "retired": []} == {**start, "retired": []}


# --- session : journal, undo/redo, reprise ------------------------------------
def test_session_undo_redo_survive_restart(tmp_path):
    s = Session.create(tmp_path / "s", tl3())
    s.do(SplitClip("s002", 70))
    s.do(ripple_delete(s.tl, "s001"), "supprimer l'ouverture")
    after_two = s.tl.canonical()
    assert s.undo() == "supprimer l'ouverture"
    after_undo = s.tl.canonical()

    reopened = Session.open(tmp_path / "s")
    assert reopened.tl.canonical() == after_undo
    assert reopened.can_undo and reopened.can_redo
    reopened.redo()
    assert reopened.tl.canonical() == after_two
    reopened.undo()
    reopened.undo()
    assert reopened.tl.canonical() == tl3().canonical() | {"retired": sorted(["s009", "s010"])}
    assert not reopened.can_undo
    assert Session.open(tmp_path / "s").tl.canonical() == reopened.tl.canonical()


def test_new_action_clears_redo_and_failed_action_leaves_no_trace(tmp_path):
    s = Session.create(tmp_path / "s", tl3())
    s.do(SetClipProps("s001", {"note": "a"}))
    s.undo()
    assert s.can_redo
    with pytest.raises(TimelineError):
        s.do(MoveClip("s003", 90))                          # chevauchement
    assert s.can_redo                                       # l'échec n'a rien touché
    s.do(SetClipProps("s001", {"note": "b"}))
    assert not s.can_redo
    assert len((tmp_path / "s" / "history.jsonl").read_text().splitlines()) == 3
    assert Session.open(tmp_path / "s").tl.clip("s001").note == "b"


def test_session_ignores_a_torn_last_journal_line(tmp_path):
    s = Session.create(tmp_path / "s", tl3())
    s.do(SetClipProps("s001", {"note": "gardé"}))
    with open(s.journal_path, "a", encoding="utf-8") as f:
        f.write('{"kind": "do", "op": {"type": "set_clip_pr')   # crash en pleine écriture
    assert Session.open(tmp_path / "s").tl.clip("s001").note == "gardé"


def test_session_refuses_a_corrupt_middle_line_and_double_create(tmp_path):
    s = Session.create(tmp_path / "s", tl3())
    s.do(SetClipProps("s001", {"note": "1"}))
    s.do(SetClipProps("s001", {"note": "2"}))
    lines = s.journal_path.read_text().splitlines()
    s.journal_path.write_text("\n".join([lines[0][:10], lines[1]]) + "\n")
    with pytest.raises(TimelineError, match="corrompu"):
        Session.open(tmp_path / "s")
    with pytest.raises(TimelineError):
        Session.create(tmp_path / "s", tl3())               # n'écrase jamais une session


def test_snapshots_never_overwrite(tmp_path):
    s = Session.create(tmp_path / "s", tl3())
    a = s.snapshot("avant client")
    s.do(SetClipProps("s001", {"note": "x"}))
    b = s.snapshot("avant client")
    assert a != b and a.exists() and b.exists()
    assert json.loads(a.read_text())["clips"][0]["note"] == "ouverture"


def test_session_current_json_follows_state(tmp_path):
    s = Session.create(tmp_path / "s", tl3())
    s.do(SplitClip("s001", 25))
    assert Timeline.from_dict(json.loads(s.current_path.read_text())).canonical() == s.tl.canonical()


# --- schéma / migrations --------------------------------------------------------
def test_schema_migration_guards():
    d = tl3().to_dict()
    assert Timeline.from_dict(d).canonical() == tl3().canonical()
    with pytest.raises(TimelineError, match="schéma"):
        migrate({**d, "schema": 99})
    bad = dict(d)
    del bad["schema"]
    with pytest.raises(TimelineError):
        migrate(bad)


def test_validate_catches_structural_errors():
    for mutate in (lambda t: setattr(t.clips[0], "duration", 0),
                   lambda t: setattr(t.clips[0], "track", "Z9"),
                   lambda t: setattr(t.clips[0], "start", 1.5),
                   lambda t: t.clips.append(t.clips[0]),
                   lambda t: t.retired.append("s001"),
                   lambda t: setattr(t.clips[0], "audio", "zzz")):
        tl = tl3()
        mutate(tl)
        with pytest.raises(TimelineError):
            tl.validate()
