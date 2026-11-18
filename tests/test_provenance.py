"""Étape 1-a : origine, provenance, groupes d'annulation, garde de portée, sélection partagée."""

import json

import pytest

from kolnoa_editor.timeline import (MoveClip, Mark, Scope, ScopeViolation, Selection, Session, SetClipProps, SplitClip,
                                    TimelineError, ripple_delete)
from test_timeline import tl3


def nr(d):
    return {k: v for k, v in d.items() if k != "retired"}


def mk(tmp_path):
    return Session.create(tmp_path / "s", tl3())


def test_defaults_protected_and_origin_states(tmp_path):
    s = mk(tmp_path)
    assert s.state_of("s001") == "manuel"                       # plan présent au départ : protégé
    s.do(SplitClip("s002", 70), origin="auto", scope=Scope(50, 100), confirmed_protected=True)
    kids = [c.id for c in s.tl.clips if c.parent == "s002" or c.id == "s002"]
    assert {s.state_of(i) for i in kids} == {"auto"}
    s.do(SetClipProps("s003", {"note": "x"}))
    assert s.state_of("s003") == "manuel"


def test_provenance_survives_reopen_and_undo(tmp_path):
    s = mk(tmp_path)
    s.do(SplitClip("s002", 70), origin="auto", scope=Scope(50, 100), confirmed_protected=True)
    snap = s.provenance()
    assert snap
    r = Session.open(tmp_path / "s")
    assert r.provenance() == snap
    assert json.loads((tmp_path / "s" / "provenance.json").read_text()) == dict(sorted(snap.items()))
    r.undo()
    assert r.provenance() == {}
    r.redo()
    assert r.provenance() == snap
    assert Session.open(tmp_path / "s").provenance() == snap


def test_protected_clip_refused_without_confirmation(tmp_path):
    s = mk(tmp_path)
    canon, journal = s.tl.canonical(), (tmp_path / "s" / "history.jsonl").read_text()
    with pytest.raises(ScopeViolation):
        s.do(SetClipProps("s002", {"note": "a"}), origin="auto", scope=Scope(50, 100))
    assert s.tl.canonical() == canon
    assert (tmp_path / "s" / "history.jsonl").read_text() == journal
    assert not s.can_undo
    s.do(SetClipProps("s002", {"note": "a"}), origin="auto", scope=Scope(50, 100), confirmed_protected=True)
    assert s.tl.clip("s002").note == "a"


def test_out_of_scope_and_missing_scope(tmp_path):
    s = mk(tmp_path)
    s.do(SplitClip("s002", 70), origin="auto", scope=Scope(50, 100), confirmed_protected=True)
    canon = s.tl.canonical()
    with pytest.raises(ScopeViolation):
        s.do(SplitClip("s001", 20), origin="auto", scope=Scope(50, 100), confirmed_protected=True)
    with pytest.raises(ScopeViolation):
        s.do(SplitClip("s003", 110), origin="auto", confirmed_protected=True)        # pas de portée
    assert s.tl.canonical() == canon


def test_ripple_scope_allows_pure_shift_only(tmp_path):
    s = mk(tmp_path)
    s.do(SplitClip("s002", 70), origin="auto", scope=Scope(50, 100), confirmed_protected=True)
    auto_ids = [i for i, st in s.provenance().items() if st == "auto"]
    victim = max(auto_ids, key=lambda i: s.tl.clip(i).start)
    with pytest.raises(ScopeViolation):
        s.do(ripple_delete(s.tl, victim), origin="auto", scope=Scope(50, 100))      # s003 décalé hors zone sans ripple
    s.do(ripple_delete(s.tl, victim), origin="auto", scope=Scope(50, 100, ripple=True))
    assert s.state_of("s003") == "manuel"                                          # un décalage n'est pas une intervention
    assert s.tl.clip("s003").start == 100 - (100 - 70)


def test_group_undo_single_journal_entry_and_replay(tmp_path):
    s = mk(tmp_path)
    base = s.tl.canonical()
    with s.group("génération", "auto", Scope(50, 125)):
        s.do(SplitClip("s002", 70), confirmed_protected=True)
        s.do(SplitClip("s003", 110), confirmed_protected=True)
    assert len(s.history()) == 2
    s.do(SetClipProps("s001", {"note": "m"}))
    with pytest.raises(TimelineError):
        s.undo_group()                                   # le dernier pas est manuel
    s.undo()
    s.undo_group()
    assert nr(s.tl.canonical()) == nr(base) and not s.can_undo
    lines = [json.loads(l) for l in (tmp_path / "s" / "history.jsonl").read_text().splitlines()]
    assert [l for l in lines if l["kind"] == "undo"][-1]["n"] == 2
    assert nr(Session.open(tmp_path / "s").tl.canonical()) == nr(base)
    s.redo(); s.redo()
    assert Session.open(tmp_path / "s").tl.canonical() == s.tl.canonical()


def test_group_rejects_manual_and_mixed_origin(tmp_path):
    s = mk(tmp_path)
    with pytest.raises(TimelineError):
        with s.group("x", "manuel"):
            pass
    with s.group("x", "auto", Scope(0, 125)):
        with pytest.raises(TimelineError):
            s.do(SplitClip("s002", 70), origin="directive", confirmed_protected=True)


def test_validate_protects_and_is_undoable(tmp_path):
    s = mk(tmp_path)
    s.do(SplitClip("s002", 70), origin="auto", scope=Scope(50, 100), confirmed_protected=True)
    ids = [i for i, st in s.provenance().items() if st == "auto"]
    s.validate_clips(ids)
    assert {s.state_of(i) for i in ids} == {"valide"}
    with pytest.raises(ScopeViolation):
        s.do(SetClipProps(ids[0], {"note": "z"}), origin="auto", scope=Scope(50, 100))
    assert Session.open(tmp_path / "s").provenance() == s.provenance()
    s.undo()
    assert {s.state_of(i) for i in ids} == {"auto"}


def test_manual_edit_of_auto_clip_becomes_manuel(tmp_path):
    s = mk(tmp_path)
    s.do(SplitClip("s002", 70), origin="auto", scope=Scope(50, 100), confirmed_protected=True)
    i = [i for i, st in s.provenance().items() if st == "auto"][0]
    s.do(SetClipProps(i, {"note": "retouche"}))
    assert s.state_of(i) == "manuel"
    s.undo()
    assert s.state_of(i) == "auto"


def test_old_journal_replays_as_manual(tmp_path):
    s = mk(tmp_path)
    s.do(SplitClip("s002", 70))
    lines = (tmp_path / "s" / "history.jsonl").read_text()
    assert "origin" not in lines
    assert Session.open(tmp_path / "s").provenance() == s.provenance()


def test_selection(tmp_path):
    s = mk(tmp_path)
    tl = s.tl
    assert Selection.point(tl, 60).clips_in(tl) == ["s002"]
    assert Selection.range(tl, 40, 110).clips_in(tl) == ["s001", "s002", "s003"]
    assert Selection.range(tl, 100, 50).clips_in(tl) == ["s002"]
    assert Selection.of_clips(tl, ["s003", "s001"]).to_scope() == Scope(0, 125, ("V1",))
    with pytest.raises(TimelineError):
        Selection.point(tl, 5).to_scope()
    sel = Selection.range(tl, 50, 100)
    assert sel.is_fresh(tl)
    s.do(SplitClip("s002", 70))
    assert not sel.is_fresh(s.tl)
    with pytest.raises(TimelineError):
        sel.require_fresh(s.tl)
    with pytest.raises(TimelineError):
        Selection.range(tl, 5, 5)
