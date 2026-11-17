"""A1-1 : registre de médias (media_id opaque, relink sans assimilation silencieuse)."""
import json
import os
import re
import itertools
from types import SimpleNamespace

import pytest

from kolnoa_editor.anchors import MediaRegistry, RegistryError, fps_to_rational, register_source_file, PRIVATE_KEYS

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def reg_with(**kw):
    r = MediaRegistry(kw.pop("path", None))
    return r


def reg(r, p, size=1000, mtime=1, **kw):
    kw.setdefault("duration_us", 5_000_000)
    kw.setdefault("fps", "25/1")
    kw.setdefault("quick_hash", "h-" + p)
    return r.register(p, size, mtime, **kw)


def test_media_id_is_uuid4_and_stable_for_same_file():
    r = MediaRegistry()
    a = reg(r, "a/rush1.mov")
    assert UUID.match(a.media_id)
    assert reg(r, "a/rush1.mov").media_id == a.media_id
    assert len(r.entries) == 1


def test_persistence_roundtrip(tmp_path):
    p = tmp_path / "data" / "media_registry.json"
    r = MediaRegistry(p)
    a = reg(r, "a.mov")
    b = reg(r, "b.mov", size=2000)
    r.save()
    r2 = MediaRegistry.load(p)
    assert r2.entries.keys() == {a.media_id, b.media_id}
    assert r2.id_for_path("b.mov") == b.media_id
    assert r2.get(a.media_id) == a
    assert not list(p.parent.glob("*.part"))


def test_load_missing_file_is_empty(tmp_path):
    assert MediaRegistry.load(tmp_path / "none.json").entries == {}


def test_corrupt_file_refused_not_reset(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("{pas du json", encoding="utf-8")
    with pytest.raises(RegistryError):
        MediaRegistry.load(p)
    assert p.read_text(encoding="utf-8") == "{pas du json"
    p.write_text(json.dumps({"schema": 99, "media": []}), encoding="utf-8")
    with pytest.raises(RegistryError):
        MediaRegistry.load(p)


def test_atomic_save_failure_keeps_previous_file(tmp_path, monkeypatch):
    p = tmp_path / "r.json"
    r = MediaRegistry(p)
    reg(r, "a.mov")
    r.save()
    before = p.read_bytes()
    reg(r, "b.mov")

    def boom(*a, **k):
        raise OSError("disque plein")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        r.save()
    assert p.read_bytes() == before


def test_replaced_file_gets_new_id_old_marked_replaced():
    r = MediaRegistry()
    old = reg(r, "a.mov", size=1000, mtime=1)
    new = reg(r, "a.mov", size=1500, mtime=2)
    assert new.media_id != old.media_id
    assert r.get(old.media_id).status == "remplacé"
    assert r.id_for_path("a.mov") == new.media_id
    assert old.media_id in r.entries                     # jamais supprimé


def test_same_size_new_mtime_is_also_a_replacement():
    r = MediaRegistry()
    old = reg(r, "a.mov", size=1000, mtime=1)
    new = reg(r, "a.mov", size=1000, mtime=2)
    assert new.media_id != old.media_id


def test_move_probable_requires_all_four_signals_and_changes_nothing():
    r = MediaRegistry()
    a = reg(r, "old/a.mov")
    r.mark_missing(set())
    snap = json.dumps([vars(e) for e in r.entries.values()], default=str)
    prop = r.propose_relink("new/a.mov", 1000, 5_000_000, "25/1", "h-old/a.mov")
    assert prop.state == "probable" and prop.media_id == a.media_id
    assert json.dumps([vars(e) for e in r.entries.values()], default=str) == snap   # proposition = lecture seule
    assert r.get(a.media_id).path_rel == "old/a.mov" and r.get(a.media_id).status == "manquant"


def test_move_confirm_keeps_id_and_history():
    r = MediaRegistry()
    a = reg(r, "old/a.mov")
    r.mark_missing(set())
    e = r.confirm_relink(a.media_id, "new\\a.mov")
    assert e.media_id == a.media_id and e.status == "present"
    assert e.path_rel == "new/a.mov" and e.locations == ["old/a.mov", "new/a.mov"]
    assert e.confirmed_by == "utilisateur"
    assert r.id_for_path("new/a.mov") == a.media_id


def test_ambiguous_two_identical_copies():
    r = MediaRegistry()
    a = reg(r, "x/a.mov", quick_hash="same")
    b = reg(r, "y/b.mov", quick_hash="same")
    r.mark_missing(set())
    prop = r.propose_relink("z/c.mov", 1000, 5_000_000, "25/1", "same")
    assert prop.state == "ambigu" and prop.media_id is None
    assert {c.media_id for c in prop.candidates} == {a.media_id, b.media_id}


def test_same_hash_different_duration_is_ambiguous_not_probable():
    r = MediaRegistry()
    reg(r, "a.mov", quick_hash="H")
    r.mark_missing(set())
    prop = r.propose_relink("n.mov", 1000, 9_000_000, "25/1", "H")
    assert prop.state == "ambigu" and "durée" in prop.candidates[0].conflicting


def test_missing_signal_downgrades_to_ambiguous():
    r = MediaRegistry()
    reg(r, "a.mov")
    r.mark_missing(set())
    assert r.propose_relink("n.mov", 1000, None, "25/1", "h-a.mov").state == "ambigu"


def test_no_candidate_and_unrelated_file():
    r = MediaRegistry()
    reg(r, "a.mov", quick_hash="A")
    r.mark_missing(set())
    assert r.propose_relink("n.mov", 7, 1, "30/1", "Z").state == "aucun"
    assert MediaRegistry().propose_relink("n.mov", 1).state == "aucun"


def test_present_media_never_proposed_for_relink():
    r = MediaRegistry()
    reg(r, "a.mov")
    assert r.propose_relink("copie.mov", 1000, 5_000_000, "25/1", "h-a.mov").state == "aucun"


def test_confirm_refused_on_collision_or_non_missing():
    r = MediaRegistry()
    a = reg(r, "a.mov")
    b = reg(r, "b.mov", size=2000)
    with pytest.raises(RegistryError):
        r.confirm_relink(a.media_id, "c.mov")            # pas manquant
    r.mark_missing({"b.mov"})
    with pytest.raises(RegistryError):
        r.confirm_relink(a.media_id, "b.mov")            # chemin déjà occupé
    assert r.get(a.media_id).status == "manquant"
    with pytest.raises(RegistryError):
        r.get("inconnu")


def test_mark_missing_never_deletes():
    r = MediaRegistry()
    a = reg(r, "a.mov")
    assert r.mark_missing(set()) == [a.media_id]
    assert r.get(a.media_id).status == "manquant" and r.id_for_path("a.mov") is None


def test_ids_never_reused_with_injected_factory():
    ids = iter(["id-1", "id-1"])
    r = MediaRegistry(id_factory=lambda: next(ids))
    reg(r, "a.mov")
    with pytest.raises(RegistryError):
        reg(r, "b.mov", size=5)


def test_portable_view_has_no_private_data():
    r = MediaRegistry()
    reg(r, "clients/dupont/rush1.mov", quick_hash="SECRETHASH")
    txt = json.dumps(r.portable_view())
    assert "dupont" not in txt and "SECRETHASH" not in txt and "rush1" not in txt
    assert not (PRIVATE_KEYS & set(r.portable_view()["media"][0]))
    assert "media-001" in txt


def test_fps_rational():
    assert fps_to_rational(25) == "25/1" and fps_to_rational(29.97) == "30000/1001"
    assert fps_to_rational(23.976) == "24000/1001" and fps_to_rational(None) is None
    assert not MediaRegistry().register("a", 1, fps="30000/1001").integer_fps


def test_register_source_file_from_metadata(tmp_path):
    sf = SimpleNamespace(rel="A/rush 1.mov", size=42, mtime_ns=9, kind="video", duration=12.5, quick_hash="q")
    probe = SimpleNamespace(base_fps=50.0, fps=49.9, is_vfr=True)
    r = MediaRegistry()
    e = register_source_file(r, sf, probe)
    assert (e.duration_us, e.fps, e.vfr, e.quick_hash) == (12_500_000, "50/1", True, "q")


def test_originals_untouched(tmp_path):
    f = tmp_path / "rush.mov"
    f.write_bytes(b"x" * 100)
    os.utime(f, ns=(10**18, 10**18))
    before = (f.read_bytes(), f.stat().st_mtime_ns)
    r = MediaRegistry(tmp_path / "data" / "media_registry.json")
    st = f.stat()
    reg(r, "rush.mov", size=st.st_size, mtime=st.st_mtime_ns)
    r.mark_missing(set())
    r.confirm_relink(next(iter(r.entries)), "rush.mov")
    r.save()
    assert (f.read_bytes(), f.stat().st_mtime_ns) == before


def test_project_registry_path(tmp_path):
    from kolnoa_editor.project import Project
    pr = Project(tmp_path, data_dir=tmp_path / "d")
    assert pr.media_registry_path == tmp_path / "d" / "media_registry.json"
