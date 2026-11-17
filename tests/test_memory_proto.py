"""Prototype EXPÉRIMENTAL de mémoire de réalisation : statistiques exactes sur des timelines construites à la main, lecture seule."""

import json
import re
from pathlib import Path

import pytest

from kolnoa_editor.experimental.memory_proto import analyze_timeline, render_report
from kolnoa_editor.timeline import (AddCrossfade, AddGroup, Clip, Crossfade, Marker, Session, Timeline, Track, TimelineError, execute, razor,
                                    remove_crossfade)

FPS = 25


def clip(cid, track, start, dur, **kw):
    kw.setdefault("media", "rush1.mp4")
    return Clip(id=cid, track=track, start=start, duration=dur, **kw)


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


# --- revue ChatGPT : cadences, arrondis, trous, chevauchements, transitions, undo, pistes secondaires, isolation -----------

@pytest.mark.parametrize("fps", [25, 50, 24, 30])
def test_exact_durations_at_each_integer_frame_rate(fps):
    tl = base([clip("c001", "V1", 0, fps * 3), clip("c002", "V1", fps * 3, fps // 2)], [Marker("m001", 0, "chapter", "a")])
    tl.fps = fps
    s = analyze_timeline(tl)["sections"][0]
    assert s["shots"]["durations_s"]["max"] == 3.0 and s["shots"]["durations_s"]["min"] == round((fps // 2) / fps, 3)
    assert s["duration_s"] == round((fps * 3 + fps // 2) / fps, 3)


def test_rounding_of_non_terminating_durations_is_stable_and_bounded():
    tl = base([clip("c001", "V1", 0, 1)], [Marker("m001", 0, "chapter", "a")])
    tl.fps = 30                                                             # 1/30 s = 0,0333… : arrondi à 3 décimales
    assert analyze_timeline(tl)["sections"][0]["shots"]["durations_s"]["median"] == 0.033


def test_gaps_do_not_count_as_shots_and_overlapping_main_tracks_are_both_counted():
    gaps = base([clip("c001", "V1", 0, 50), clip("c002", "V1", 200, 50)], [Marker("m001", 0, "chapter", "a")])
    s = analyze_timeline(gaps)["sections"][0]
    assert s["shots"]["n"] == 2 and s["duration_s"] == 10.0 and s["angle_switches"] == 0
    two_main = base([clip("c001", "V1", 0, 100), clip("c002", "V3", 0, 100)], [Marker("m001", 0, "chapter", "a")],
                    tracks=[Track("V1", "video", "main"), Track("V3", "video", "main")])
    assert analyze_timeline(two_main)["sections"][0]["shots"]["n"] == 2   # limite connue, documentée : pas de dédoublonnage


def test_clip_is_counted_in_the_section_where_it_starts_even_if_it_straddles():
    tl = base([clip("c001", "V1", 0, 150), clip("c002", "V1", 150, 100)],
              [Marker("m001", 0, "chapter", "a"), Marker("m002", 100, "chapter", "b")])
    a, b = analyze_timeline(tl)["sections"]
    assert (a["shots"]["n"], b["shots"]["n"]) == (1, 1)                    # c001 [0,150) reste dans « a » bien qu'il déborde


def test_secondary_track_clip_is_structural_not_semantic():
    tl = base([clip("c001", "V1", 0, 100), clip("k001", "V2", 10, 20)], [Marker("m001", 0, "chapter", "a")])
    assert analyze_timeline(tl)["sections"][0]["cutaways"]["n"] == 1
    tl.track("V2").role = "main"                                           # le même plan n'est plus un « plan de coupe »
    s = analyze_timeline(tl)["sections"][0]
    assert s["cutaways"]["n"] == 0 and s["shots"]["n"] == 2


def test_transition_is_seen_then_gone_after_undo_through_a_real_session(tmp_path):
    tl = base([clip("c001", "V1", 0, 100), clip("c002", "V1", 100, 100, src_in=10.0)], [Marker("m001", 0, "chapter", "a")])
    s = Session.create(tmp_path / "sess", tl)
    before = analyze_timeline(s.tl)
    s.do(AddCrossfade(
        {"id": "x001", "left": "c001", "right": "c002", "duration": 0.5, "curve": "equal_power"}))
    assert analyze_timeline(s.tl)["sections"][0]["crossfades_audio"]["n"] == 1
    s.undo()
    assert analyze_timeline(s.tl) == before
    s.redo()
    assert analyze_timeline(s.tl)["sections"][0]["crossfades_audio"]["n"] == 1


def test_split_by_razor_changes_cuts_but_not_total_duration():
    tl = base([clip("c001", "V1", 0, 200)], [Marker("m001", 0, "chapter", "a")])
    before = analyze_timeline(tl)["sections"][0]
    execute(tl, razor(tl, 100, ["V1"]))
    after = analyze_timeline(tl)["sections"][0]
    assert after["shots"]["n"] == before["shots"]["n"] + 1 and after["duration_s"] == before["duration_s"]


def test_no_production_module_imports_the_experimental_package():
    src = Path(__file__).resolve().parents[1] / "src" / "kolnoa_editor"
    offenders = [str(p.relative_to(src)) for p in src.rglob("*.py")
                 if "experimental" not in p.parts and re.search(r"^\s*(from|import)\s+[\w.]*experimental\b", p.read_text(encoding="utf-8"), re.M)]
    assert offenders == []


def test_hold_threshold_is_configurable_per_section_kind():
    tl = base([clip("c001", "V1", 0, 100), clip("c002", "V1", 100, 100), clip("d001", "V1", 300, 100)],
              [Marker("m001", 0, "chapter", "a"), Marker("m002", 300, "chapter", "b")])
    st = analyze_timeline(tl, hold_s={"a": 3.0, "default": 10.0})            # plans de 4 s
    a, b = st["sections"]
    assert (a["hold_s"], a["hold_ratio"]) == (3.0, 1.0) and (b["hold_s"], b["hold_ratio"]) == (10.0, 0.0)
