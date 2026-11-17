"""Prototype de mémoire de réalisation : statistiques exactes sur des timelines construites à la main, lecture seule."""

import json

import pytest

from kolnoa_editor.memory import analyze_timeline, render_report
from kolnoa_editor.timeline import Clip, Crossfade, Marker, Timeline, Track

FPS = 25


def clip(cid, track, start, dur, **kw):
    return Clip(id=cid, track=track, media="rush1.mp4", start=start, duration=dur, **kw)


def base(clips, markers=(), tracks=None, crossfades=()):
    tl = Timeline(title="T", fps=FPS, tracks=tracks or [Track("V1", "video", "main"), Track("V2", "video", "coupe")],
                  clips=list(clips), markers=list(markers), crossfades=list(crossfades))
    tl.validate()
    return tl


def test_sections_durations_and_rates_are_exact():
    # section « ceremonie » [0,500) = 20 s, 4 plans de 125 i = 5 s ; section « danses » [500,750) = 10 s, 5 plans de 50 i = 2 s
    clips = [clip(f"c{i:03d}", "V1", i * 125, 125) for i in range(4)] + \
            [clip(f"d{i:03d}", "V1", 500 + i * 50, 50) for i in range(5)]
    tl = base(clips, [Marker("m001", 0, "chapter", "ceremonie"), Marker("m002", 500, "chapter", "danses")])
    st = analyze_timeline(tl)
    cer, dan = st["sections"]
    assert (cer["kind"], cer["duration_s"], cer["shots"]["n"]) == ("ceremonie", 20.0, 4)
    assert cer["shots"]["durations_s"]["median"] == 5.0
    assert cer["cuts_per_min"] == pytest.approx(4 / (20 / 60), rel=1e-3)
    assert (dan["kind"], dan["duration_s"], dan["shots"]["n"]) == ("danses", 10.0, 5)
    assert dan["shots"]["durations_s"]["median"] == 2.0
    assert dan["cuts_per_min"] > cer["cuts_per_min"]                      # le rythme varie selon la section
    assert st["global"]["n_shots"] == 9


def test_analysis_does_not_modify_the_timeline_and_is_deterministic():
    tl = base([clip("c001", "V1", 0, 100), clip("c002", "V1", 100, 100)], [Marker("m001", 0, "chapter", "a")])
    before = json.dumps(tl.to_dict(), sort_keys=True)
    a, b = analyze_timeline(tl), analyze_timeline(tl)
    assert json.dumps(tl.to_dict(), sort_keys=True) == before
    assert a == b


def test_cutaways_and_hold_ratio_and_crossfades():
    clips = [clip("c001", "V1", 0, 250), clip("c002", "V1", 250, 50, src_in=10.0), clip("c003", "V1", 300, 50, src_in=12.0),   # 10 s, 2 s, 2 s
             clip("k001", "V2", 100, 25), clip("k002", "V2", 200, 50)]
    tl = base(clips, [Marker("m001", 0, "chapter", "x")], crossfades=[Crossfade("x001", "c001", "c002", 0.5)])
    s = analyze_timeline(tl, hold_s=6.0)["sections"][0]
    assert s["cutaways"]["n"] == 2 and s["cutaways"]["durations_s"]["median"] == 1.5
    assert s["hold_ratio"] == pytest.approx(10 / 14, abs=1e-3)           # 10 s sur 14 s dans des plans ≥ 6 s
    assert s["crossfades_audio"] == {"n": 1, "durations_s": s["crossfades_audio"]["durations_s"]}
    assert s["crossfades_audio"]["durations_s"]["median"] == 0.5


def test_no_chapter_gives_one_section_and_a_warning():
    st = analyze_timeline(base([clip("c001", "V1", 0, 50)]))
    assert [s["kind"] for s in st["sections"]] == ["(sans chapitre)"]
    assert any("chapter" in w for w in st["warnings"])


def test_empty_timeline_is_reported_not_crashing():
    st = analyze_timeline(base([]))
    assert st["global"]["n_shots"] == 0 and st["sections"][0]["shots"]["durations_s"] is None
    assert any("rien à mesurer" in w for w in st["warnings"])


def test_report_has_no_media_name_and_lists_what_is_not_measured():
    tl = base([clip("c001", "V1", 0, 100)], [Marker("m001", 0, "chapter", "mairie")])
    st = analyze_timeline(tl)
    text = render_report(st) + json.dumps(st)
    assert "rush1" not in text and ".mp4" not in text
    assert "ralentis" in st["unmeasured"] and st["angle_roles"] is None
