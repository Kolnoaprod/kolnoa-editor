"""Rendu à vitesse constante : mapping source, vérification durée, tempo audio."""
import pytest

from kolnoa_editor.timeline.model import Timeline, Track, Clip
from kolnoa_editor.timeline.flatten import flatten_video, check_flatten
from kolnoa_editor.timeline.audio_mix import build_mix_plan, build_filtergraph, AudioInfo


def timeline(speed=0.5, audio="source"):
    tl = Timeline(title="slow", fps=25, resolution=(320, 180))
    tl.tracks = [Track(id="V1", kind="video")]
    tl.clips = [Clip(id="s001", track="V1", media="C0001.MP4", start=0,
                     duration=100, src_in=2.0, audio=audio, extra={"speed": speed})]
    return tl


def test_slow_motion_span_consumes_half_source_duration():
    tl = timeline()
    span = flatten_video(tl).spans[0]
    assert span.speed == 0.5
    assert span.src_in == 2.0
    assert span.duration == 100
    assert not [i for i in check_flatten(tl, source_len=lambda _: 4.0) if i.level == "error"]


def test_slow_motion_source_bounds_and_invalid_speed():
    tl = timeline(2.0)
    issues = check_flatten(tl, source_len=lambda _: 9.0)
    assert any("dépasse la fin" in i.message for i in issues)
    tl.clips[0].extra["speed"] = 0
    assert any("vitesse invalide" in i.message for i in check_flatten(tl))


def test_audio_source_uses_pitch_preserving_atempo():
    tl = timeline()
    plan = build_mix_plan(tl)
    assert len(plan.events) == 1 and plan.events[0].tempo == 0.5
    _, graph = build_filtergraph(plan, {"C0001.MP4": AudioInfo(2, 48000, 10)}, 4)
    assert "atrim=start=2.000000:duration=2.000000" in graph
    assert "atempo=0.50000000" in graph


def test_multicam_group_audio_speed_requires_separate_implementation():
    tl = timeline(audio="group")
    assert any("audio maître" in i.message for i in check_flatten(tl))
