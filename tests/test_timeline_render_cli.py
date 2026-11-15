"""La timeline rend exactement comme la liste de décisions, et la CLI la pilote sans rien casser."""

import json

import pytest

from kolnoa_editor.cli import main
from kolnoa_editor.decision_list import DecisionList, Segment
from kolnoa_editor.probe import probe
from kolnoa_editor.project import Project
from kolnoa_editor.renderer import render
from kolnoa_editor.timeline import Session, SplitClip, from_decision_list, ripple_delete, to_decision_list

SONY = "MAIRIE/A7IV/20261011_C4837.MP4"
CANON = "SOIREE/CANON/A_0009C238A261108_113335HL_CANON.MP4"


def make_dl():
    return DecisionList(title="Parité", version=1, resolution=(640, 360), segments=[
        Segment("s001", SONY, 1.0, 3.0, fade_in=0.2),
        Segment("s002", CANON, 0.0, 1.52, audio="mute"),       # durée hors grille : 38 images
        Segment("s003", SONY, 4.0, 5.0, fade_out=0.4)])


def frames(path):
    info = probe(path)
    return round(info.duration * info.fps)


def test_timeline_render_matches_decision_list_render(mariage):
    project = Project(mariage)
    project.ensure()
    dl = make_dl()
    direct = render(project, dl, "final")
    via_timeline = render(project, to_decision_list(from_decision_list(dl))[0], "final")
    assert frames(via_timeline) == frames(direct) == 50 + 38 + 25
    assert probe(via_timeline).duration == pytest.approx(probe(direct).duration, abs=0.05)


def test_edit_then_render_changes_the_film_as_expected(mariage):
    project = Project(mariage)
    project.ensure()
    s = Session.create(project.timeline_dir, from_decision_list(make_dl()))
    s.do(ripple_delete(s.tl, "s002"))
    s.do(SplitClip("s001", 25))
    dl, _ = to_decision_list(s.tl)
    assert [x.id for x in dl.segments] == ["s001", "s004", "s003"]
    out = render(project, dl, "final")
    assert frames(out) == 50 + 25


def test_cli_import_edit_undo_export(mariage, tmp_path, capsys):
    dl_file = tmp_path / "v1.json"
    make_dl().save(dl_file)
    root = str(mariage)
    assert main(["timeline-import", root, str(dl_file)]) == 0
    assert main(["timeline-import", root, str(dl_file)]) == 2          # n'écrase jamais
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps({"label": "couper la fin", "ops": [{"type": "remove_clip", "clip_id": "s003"}]}))
    assert main(["timeline-apply", root, str(ops)]) == 0
    capsys.readouterr()
    assert main(["timeline-show", root]) == 0
    shown = capsys.readouterr().out
    assert "s003" not in shown.split("Historique")[0] and "1 action" in shown
    assert main(["timeline-undo", root]) == 0
    out = tmp_path / "back.json"
    assert main(["timeline-export", root, str(out)]) == 0
    assert DecisionList.load(out).to_dict() == make_dl().to_dict()
    assert main(["timeline-export", root, str(out)]) == 2              # n'écrase jamais
    assert main(["timeline-redo", root]) == 0
    assert main(["timeline-redo", root]) == 2                          # plus rien à rétablir


def test_cli_apply_rejects_invalid_ops_without_changing_anything(mariage, tmp_path):
    dl_file = tmp_path / "v1.json"
    make_dl().save(dl_file)
    root = str(mariage)
    main(["timeline-import", root, str(dl_file)])
    before = (Project(mariage).timeline_dir / "history.jsonl").read_text()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"type": "remove_clip", "clip_id": "s001"}, {"type": "remove_clip", "clip_id": "nope"}]))
    assert main(["timeline-apply", root, str(bad)]) == 2
    assert (Project(mariage).timeline_dir / "history.jsonl").read_text() == before
    assert "s001" in {c.id for c in Session.open(Project(mariage).timeline_dir).tl.clips}


def test_cli_force_import_keeps_the_old_timeline(mariage, tmp_path):
    dl_file = tmp_path / "v1.json"
    make_dl().save(dl_file)
    root = str(mariage)
    main(["timeline-import", root, str(dl_file)])
    assert main(["timeline-import", root, str(dl_file), "--force"]) == 0
    olds = list(Project(mariage).data.glob("timeline.old-*"))
    assert len(olds) == 1 and (olds[0] / "base.json").exists()
