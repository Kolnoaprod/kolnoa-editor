"""Filiation des ids de segments, versions et retours reportés."""


import pytest

from kolnoa_editor.comments import add_comment, load_comments, save_comments, to_prompt
from kolnoa_editor.decision_list import DecisionList, DecisionListError, Segment, diff
from kolnoa_editor.project import Project
from kolnoa_editor.scanner import refresh
from kolnoa_editor.versions import import_version, load_version

SONY = "MAIRIE/A7IV/20261011_C4837.MP4"      # 6 s
CANON = "SOIREE/CANON/A_0009C238A261108_113335HL_CANON.MP4"  # 4 s


def base():
    return DecisionList(title="L", version=1, resolution=(640, 360), segments=[
        Segment("s001", SONY, 0.0, 6.0, fade_in=0.2, fade_out=0.4, note="ouverture"),
        Segment("s002", CANON, 0.0, 2.0)])


def test_split_keeps_first_id_and_records_parent():
    dl = base()
    new = dl.split("s001", 2.5)
    assert new == "s003"                                   # s002 existe déjà
    left, right = dl.get("s001"), dl.get(new)
    assert (left.start, left.end, left.fade_in, left.fade_out) == (0.0, 2.5, 0.2, 0.0)
    assert (right.start, right.end, right.fade_in, right.fade_out) == (2.5, 6.0, 0.0, 0.4)
    assert right.parent == "s001" and right.note == "ouverture"
    assert [s.id for s in dl.segments] == ["s001", "s003", "s002"]
    assert dl.duration == pytest.approx(8.0)               # la scission ne change pas la durée
    assert dl.descends_from("s003", "s001")
    dl.validate()


def test_split_rejects_bad_points():
    dl = base()
    for at in (0.0, 0.01, 6.0, 7.0):
        with pytest.raises(DecisionListError):
            dl.split("s001", at)


def test_removed_ids_are_never_reused():
    dl = base()
    dl.remove("s002")
    assert dl.retired == ["s002"] and dl.next_id() == "s003"
    with pytest.raises(DecisionListError):
        dl.insert(Segment("s002", CANON, 0, 1))
    dl.segments.append(Segment("s002", CANON, 0, 1))  # contournement manuel : la validation l'attrape
    with pytest.raises(DecisionListError, match="supprimé"):
        dl.validate()


def test_lineage_survives_json_roundtrip(tmp_path):
    dl = base()
    dl.split("s001", 3.0)
    dl.remove("s002")
    path = tmp_path / "v.json"
    dl.save(path)
    back = DecisionList.load(path)
    assert back == dl and back.retired == ["s002"] and back.get("s003").parent == "s001"
    legacy = {"schema": 1, "title": "old", "segments": [
        {"id": "s001", "clip": SONY, "start": 0, "end": 1}]}
    assert DecisionList.from_dict(legacy).segments[0].parent is None


def test_diff_lists_added_removed_trimmed_reordered():
    old = base()
    new = base()
    new.remove("s002")
    new.insert(Segment("s010", CANON, 1.0, 2.0, parent=None))
    new.get("s001").end = 4.0
    lines = diff(old, new)
    assert any(l.startswith("- s002") for l in lines)
    assert any(l.startswith("+ s010") for l in lines)
    assert any(l.startswith("~ s001") and "plage" in l for l in lines)
    assert any(l.startswith("= durée") for l in lines)
    assert diff(old, base()) == []


def _import(project, tmp_path, dl, name="in.json"):
    path = tmp_path / name
    dl.save(path)
    return import_version(project, path)


def test_import_assigns_versions_and_never_overwrites(mariage, tmp_path):
    project = Project(mariage)
    r1 = _import(project, tmp_path, base())
    assert r1.dl.version == 1 and r1.previous is None
    wrong = base()
    wrong.version = 1  # l'IA se trompe de numéro
    r2 = _import(project, tmp_path, wrong)
    assert r2.dl.version == 2
    assert load_version(project, 1).version == 1 and project.version_path(1).is_file()


def test_comments_follow_split_trim_and_removal(mariage, tmp_path):
    project = Project(mariage)
    v1 = _import(project, tmp_path, base()).dl
    c_split = add_comment(project, v1, 4.0, "plan trop long ici")   # s001 à 4,0 s
    c_trim = add_comment(project, v1, 1.0, "bien")                  # s001 à 1,0 s
    c_gone = add_comment(project, v1, 7.0, "changer ce plan")       # s002 à 1,0 s
    c_done = add_comment(project, v1, 0.5, "ok déjà fait")
    comments = load_comments(project, 1)
    comments[-1].status = "done"
    save_comments(project, 1, comments)

    v2 = base()
    new_id = v2.split("s001", 3.0)          # s001 [0,3) + s003 [3,6)
    v2.get("s001").end = 2.0                # le premier morceau est raccourci : 1,0 s reste dedans
    v2.remove("s002")                       # le plan commenté disparaît
    r = _import(project, tmp_path, v2)
    assert r.dl.version == 2 and r.dl.retired == ["s002"]
    carried = {c.id: c for c in load_comments(project, 2)}

    assert set(carried) == {c_split.id, c_trim.id, c_gone.id}        # le retour traité n'est pas reporté
    assert carried[c_split.id].segment_id == new_id and carried[c_split.id].anchor == "exact"
    assert carried[c_split.id].source_time == pytest.approx(4.0)
    assert carried[c_split.id].render_time == pytest.approx(r.dl.start_of(new_id) + 1.0)
    assert carried[c_trim.id].segment_id == "s001" and carried[c_trim.id].anchor == "exact"
    assert carried[c_gone.id].status == "orphan" and carried[c_gone.id].render_time is None
    prompt = to_prompt(project, r.dl)
    assert "supprimé" in prompt and "retired" in prompt and c_split.text in prompt


def test_comment_on_trimmed_segment_falls_back_to_segment_anchor(mariage, tmp_path):
    project = Project(mariage)
    v1 = _import(project, tmp_path, base()).dl
    c = add_comment(project, v1, 4.0, "ici")
    v2 = base()
    v2.get("s001").end = 2.0                                          # le point 4,0 s n'est plus dans le plan
    _import(project, tmp_path, v2)
    moved = load_comments(project, 2)[0]
    assert moved.id == c.id and moved.anchor == "segment" and moved.segment_id == "s001"


def test_import_refuses_reused_retired_id_and_warns_on_clip_change(mariage, tmp_path):
    project = Project(mariage)
    _import(project, tmp_path, base())
    v2 = base()
    v2.remove("s002")
    _import(project, tmp_path, v2)
    v3 = base()                              # ré-introduit s002, supprimé en v2
    with pytest.raises(DecisionListError, match="réutilisé"):
        _import(project, tmp_path, v3)
    v3 = base()
    v3.remove("s002")
    v3.get("s001").clip = CANON              # même id, autre clip
    r = _import(project, tmp_path, v3)
    assert any("change de clip" in w for w in r.warnings)


def test_new_comment_ids_do_not_collide_with_carried_ones(mariage, tmp_path):
    project = Project(mariage)
    v1 = _import(project, tmp_path, base()).dl
    add_comment(project, v1, 1.0, "a")
    add_comment(project, v1, 2.0, "b")
    v2 = _import(project, tmp_path, base()).dl
    c = add_comment(project, v2, 3.0, "c")
    assert c.id == "c003"
    assert len({x.id for x in load_comments(project, 2)}) == 3


def test_data_dir_can_live_outside_the_drive(mariage, tmp_path, monkeypatch):
    monkeypatch.setenv("KOLNOA_DATA_DIR", str(tmp_path / "donnees"))
    project = Project(mariage)
    refresh(project, probe_media=True)
    assert project.data.is_relative_to(tmp_path / "donnees")
    assert project.library_path.is_file()
    assert not (mariage / ".kolnoa").exists()                 # rien d'écrit dans le dossier du mariage


def test_corrupt_library_is_quarantined_not_overwritten(mariage):
    project = Project(mariage)
    project.ensure()
    project.library_path.write_text("{ceci n'est pas du json", encoding="utf-8")
    files, _ = refresh(project)
    assert len(files) == 5
    assert list(project.data.glob("library.json.corrupt-*"))


def test_quick_hash_is_stable_and_detects_content_change(mariage):
    project = Project(mariage)
    files, _ = refresh(project, with_hash=True)
    h1 = {f.rel: f.quick_hash for f in files}
    assert all(h1.values())
    files, report = refresh(project, with_hash=True)
    assert report.unchanged == 5 and {f.rel: f.quick_hash for f in files} == h1
    target = mariage / "musique.mp3"
    data = bytearray(target.read_bytes())
    data[len(data) // 2] ^= 0xFF
    target.write_bytes(bytes(data))
    files, report = refresh(project, with_hash=True)
    assert report.changed == ["musique.mp3"]
    assert {f.rel: f.quick_hash for f in files}["musique.mp3"] != h1["musique.mp3"]


def test_missing_files_are_flagged_then_recovered_not_deleted(mariage):
    project = Project(mariage)
    refresh(project)
    target = mariage / "musique.mp3"
    saved = target.read_bytes()
    stat = target.stat()
    target.unlink()
    files, report = refresh(project)
    assert report.missing == ["musique.mp3"]
    assert {f.rel: f.missing for f in files}["musique.mp3"] is True
    target.write_bytes(saved)
    import os

    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    files, report = refresh(project)
    assert report.recovered == ["musique.mp3"] and not any(f.missing for f in files)
    target.unlink()
    files, report = refresh(project, prune=True)
    assert report.removed == ["musique.mp3"] and "musique.mp3" not in {f.rel for f in files}
