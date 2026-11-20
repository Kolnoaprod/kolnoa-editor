from kolnoa_editor.auto.analysis import RushInfo
from kolnoa_editor.auto.condensed import BalancedThresholds, MediaScope, select_portions
from kolnoa_editor.auto.multicam import propose_groups
from kolnoa_editor.auto.planner import StyleParams
from kolnoa_editor.auto.visual import Sample


def test_selects_good_portion_after_blur_and_before_clipping():
    rush = RushInfo("A7IV/a.mp4", 10, has_audio=False)
    samples = [Sample(i + .5, 0 if i < 2 else 200, 128, 0, 1 if i >= 8 else 0) for i in range(10)]
    shots, notes = select_portions(rush, samples, 1, StyleParams(), BalancedThresholds())
    assert [(shot.src_in, shot.length) for shot in shots] == [(2, 6)]
    assert any("netteté" in note for note in notes)
    assert any("écrêtées" in note for note in notes)


def test_cross_folder_proposals_use_time_overlap_and_explicit_media_scope():
    a = RushInfo("A7IV/a.mp4", 15, "2026-11-20T08:00:00", "A7IV")
    b = RushInfo("OSMO/b.mp4", 15, "2026-11-20T08:00:02", "OSMO")
    c = RushInfo("A7IV/c.mp4", 15, "2026-11-20T08:01:00", "A7IV")
    scope = MediaScope(frozenset((a.media, b.media)))
    assert not propose_groups([a, b])
    assert not propose_groups([a, b, c], min_overlap=20, across_folders=True)
    groups = propose_groups([rush for rush in (a, b, c) if scope.includes(rush)], across_folders=True)
    assert len(groups) == 1 and groups[0].members == [a, b]


def test_condensed_stages_an_edit_and_reports_portions(tmp_path, monkeypatch):
    from kolnoa_editor.app import ProjectController
    from kolnoa_editor.ffmpeg import CancelToken

    ctl = ProjectController.open(tmp_path, tmp_path / "project")
    a = RushInfo("A7IV/a.mp4", 10, has_audio=False)
    b = RushInfo("OSMO/b.mp4", 10, has_audio=False)
    ctl.add_rushes([a, b])
    monkeypatch.setattr("kolnoa_editor.app.controller.analyze_angle", lambda *a, **kw: [
        Sample(i + .5, 0 if i < 2 else 200, 128, 0, 0) for i in range(10)
    ])
    draft, report = ctl.prepare_auto_edit(
        "Monte-moi cette séquence", None, cancel=CancelToken(), on_progress=lambda *args: None,
        media_scope=MediaScope(frozenset((a.media, b.media))), balanced=BalancedThresholds(), synchronize=False,
    )
    assert not ctl.tl.clips
    ctl.session.adopt(draft.session)
    assert {clip.media for clip in ctl.tl.clips_on("V1")} == {a.media, b.media}
    assert report["duration"] == 12 and report["used"] == 2
    reopened = ProjectController.open(tmp_path, tmp_path / "project")
    assert reopened.tl.canonical() == ctl.tl.canonical()
