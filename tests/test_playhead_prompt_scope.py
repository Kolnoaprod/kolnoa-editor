"""Tests du ciblage non destructif des retours depuis la barre de lecture."""
import pytest

from kolnoa_editor.auto.playhead import selection_from_playhead
from kolnoa_editor.timeline.model import Clip, Timeline, TimelineError, Track


@pytest.fixture
def tl():
    return Timeline(
        title="retours", fps=25, resolution=(320, 180),
        tracks=[Track("V1", "video"), Track("V2", "video"), Track("A1", "audio")],
        clips=[
            Clip("intro", "V1", "a.mp4", 0, 50, 0.0),
            Clip("suite", "V1", "b.mp4", 50, 50, 0.0),
            Clip("overlay", "V2", "c.mp4", 25, 25, 0.0),
        ],
    )


def test_playhead_targets_one_clip_and_boundary(tl):
    sel = selection_from_playhead(tl, 49)
    assert sel.kind == "clips" and sel.clip_ids == ("intro",)
    assert selection_from_playhead(tl, 50).clip_ids == ("suite",)
    assert sel.is_fresh(tl)


def test_explicit_range_does_not_expand_to_whole_film(tl):
    sel = selection_from_playhead(tl, 45, range_end=55)
    assert sel.kind == "range"
    assert (sel.start, sel.end) == (45, 55)
    assert sel.clips_in(tl) == ["intro", "suite"]


def test_video_track_is_explicit(tl):
    assert selection_from_playhead(tl, 30, track="V2").clip_ids == ("overlay",)
    with pytest.raises(TimelineError, match="piste vidéo"):
        selection_from_playhead(tl, 30, track="A1")


def test_outside_clip_and_invalid_range_fail_closed(tl):
    for frame in (-1, 100, 101):
        with pytest.raises(TimelineError):
            selection_from_playhead(tl, frame)
    with pytest.raises(TimelineError):
        selection_from_playhead(tl, 100, range_end=120)
    with pytest.raises(TimelineError):
        selection_from_playhead(tl, 50, range_end=50)
