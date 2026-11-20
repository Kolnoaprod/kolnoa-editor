import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from kolnoa_editor.timeline import TimelineError
from kolnoa_editor.ui.side_panels import speed_transaction
from kolnoa_editor.timeline.model import Clip, Timeline, Track


def _tl():
    tl = Timeline("t", fps=25)
    tl.tracks.append(Track("V1", "video"))
    tl.clips.append(Clip("c1", "V1", "a.mp4", 0, 100, 0.0))
    return tl


def test_speed_ok_and_reset():
    tl = _tl()
    tx = speed_transaction(tl, ["c1"], 0.5, lambda m: 60.0)
    tx.apply(tl)
    assert tl.clip("c1").extra["speed"] == 0.5
    tx = speed_transaction(tl, ["c1"], 1.0, lambda m: 60.0)
    tx.apply(tl)
    assert "speed" not in tl.clip("c1").extra


def test_speed_refused_out_of_range_or_past_rush_end():
    tl = _tl()
    with pytest.raises(TimelineError):
        speed_transaction(tl, ["c1"], 3.0)
    with pytest.raises(TimelineError):
        speed_transaction(tl, ["c1"], 2.0, lambda m: 5.0)      # 4 s à 2× = 8 s de source > 5 s
