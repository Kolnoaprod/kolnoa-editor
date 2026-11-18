"""J1-a : aplatissement vidéo — oracle image par image + scénarios aléatoires reproductibles."""
import os
import random

import pytest

from kolnoa_editor.timeline.flatten import check_flatten, flatten_video, visible_video_tracks
from kolnoa_editor.timeline.model import Clip, Timeline, Track
from kolnoa_editor.timeline.timebase import frames_to_seconds


def C(id, track, start, dur, media="a.mov", src_in=10.0, **kw):
    return Clip(id=id, track=track, media=media, start=start, duration=dur, src_in=src_in, **kw)


def TL(clips, tracks=("V1", "V2"), fps=25, **kw):
    tl = Timeline(title="t", fps=fps, tracks=[Track(t) for t in tracks], clips=clips)
    for k, v in kw.items():
        setattr(tl, k, v)
    return tl


def oracle(tl):
    """Pour chaque image : (clip visible, image du plan) par balayage exhaustif ; None = noir."""
    order = visible_video_tracks(tl)
    vis = [c for c in tl.clips if c.track in order]
    total = max((c.end for c in vis), default=0)
    out = []
    for f in range(total):
        cands = [c for c in vis if c.start <= f < c.end]
        top = max(cands, key=lambda c: order.index(c.track), default=None)
        out.append(None if top is None else (top.id, f - top.start))
    return out


def expand_fps(fl, fps=None):
    return [None if s.is_black else (s.clip_id, s.offset + k) for s in fl.spans for k in range(s.duration)]


def test_single_track_contiguous():
    tl = TL([C("a", "V1", 0, 50), C("b", "V1", 50, 25, src_in=20.0)])
    fl = flatten_video(tl)
    assert [(s.start, s.end, s.clip_id) for s in fl.spans] == [(0, 50, "a"), (50, 75, "b")] and fl.total == 75 and not fl.gaps


def test_cutaway_splits_the_base_clip_and_keeps_source_continuity():
    tl = TL([C("base", "V1", 0, 200, src_in=100.0), C("cut", "V2", 50, 30, media="b.mov", src_in=5.0)])
    fl = flatten_video(tl)
    assert [(s.start, s.end, s.clip_id) for s in fl.spans] == [(0, 50, "base"), (50, 80, "cut"), (80, 200, "base")]
    last = fl.spans[2]
    assert last.src_in == 100.0 + 80 / 25 and last.offset == 80          # la source du plan de base continue après le plan de coupe
    assert fl.spans[1].media == "b.mov" and fl.spans[1].src_in == 5.0


def test_gap_is_explicit_black_with_warning():
    tl = TL([C("a", "V1", 10, 20), C("b", "V1", 40, 10)])
    fl = flatten_video(tl)
    assert [(s.start, s.end, s.is_black) for s in fl.spans] == [(0, 10, True), (10, 30, False), (30, 40, True), (40, 50, False)]
    assert fl.gaps == [(0, 10), (30, 40)] and any("noir" in w for w in fl.warnings)


def test_muted_video_track_is_hidden_and_audio_tracks_ignored():
    tl = TL([C("a", "V1", 0, 50), C("x", "V2", 10, 10), C("m", "A1", 0, 50)], tracks=("V1", "V2", "A1"))
    tl.tracks[1].muted = True
    tl.tracks[2].kind = "audio"
    fl = flatten_video(tl)
    assert [(s.start, s.end, s.clip_id) for s in fl.spans] == [(0, 50, "a")] and any("muettes" in w for w in fl.warnings)


def test_multicam_clip_is_a_plain_clip():
    tl = TL([C("m1", "V1", 0, 25, media="camA.mov", group="g1", angle="A"), C("m2", "V1", 25, 25, media="camB.mov", src_in=3.0,
                                                                          group="g1", angle="B")])
    assert [s.media for s in flatten_video(tl).spans] == ["camA.mov", "camB.mov"]


def test_empty_timeline():
    fl = flatten_video(TL([]))
    assert fl.spans == [] and fl.total == 0 and fl.at(0) is None


def test_at_lookup():
    fl = flatten_video(TL([C("a", "V1", 0, 50), C("c", "V2", 20, 10)]))
    assert fl.at(0).clip_id == "a" and fl.at(25).clip_id == "c" and fl.at(30).clip_id == "a" and fl.at(49).clip_id == "a" and fl.at(50) is None


def test_check_reports_errors_and_unused_clips():
    tl = TL([C("a", "V1", 0, 50), C("hidden", "V1", 50, 10), C("top", "V2", 50, 10), C("sp", "V1", 60, 10, extra={"speed": 2.0})])
    issues = check_flatten(tl, media_exists=lambda m: m != "gone.mov", source_len=lambda m: 4.0)
    msgs = " | ".join(f"{i.level}:{i.message}" for i in issues)
    assert "hidden est entièrement recouvert" in msgs and "dépasse la fin de la source" in msgs
    assert "vitesse" not in msgs  # 2× est désormais une vitesse supportée
    tl2 = TL([C("a", "V1", 0, 10, media="gone.mov")])
    assert any(i.level == "error" and "introuvable" in i.message for i in check_flatten(tl2, media_exists=lambda m: False))


def random_tl(seed):
    r = random.Random(seed)
    ntr = r.randint(1, 4)
    tl = TL([], tracks=tuple(f"V{i}" for i in range(ntr)), fps=r.choice((24, 25, 30, 50, 60)))
    n = 0
    for t in tl.tracks:
        pos = r.randint(0, 40)
        for _ in range(r.randint(0, 5)):
            dur = r.randint(1, 80)
            tl.clips.append(C(f"c{n}", t.id, pos, dur, media=r.choice(("a.mov", "b.mov")), src_in=round(r.randint(0, 5000) / tl.fps, 6)))
            n += 1
            pos += dur + r.choice((0, 0, r.randint(1, 30)))
    if r.random() < 0.2 and tl.tracks:
        tl.tracks[r.randrange(len(tl.tracks))].muted = True
    return tl


def test_random_scenarios_match_oracle():
    n = int(os.environ.get("KOLNOA_FLATTEN_SCENARIOS", "3000"))
    for seed in range(n):
        tl = random_tl(seed)
        fl = flatten_video(tl)
        assert expand_fps(fl, tl.fps) == oracle(tl), f"scénario {seed}"
        assert sum(s.duration for s in fl.spans) == fl.total
        clips = {c.id: c for c in tl.clips}
        for sp in fl.spans:
            if not sp.is_black:
                c = clips[sp.clip_id]
                assert sp.src_in == round(c.src_in + frames_to_seconds(sp.offset, tl.fps), 6) and sp.media == c.media
        assert all(a.end == b.start for a, b in zip(fl.spans, fl.spans[1:])), f"scénario {seed} : segments non contigus"
        assert all(s.duration > 0 for s in fl.spans)


def test_is_pure_and_deterministic():
    tl = random_tl(42)
    before = [vars(c).copy() for c in tl.clips]
    a, b = flatten_video(tl), flatten_video(tl)
    assert a == b and [vars(c) for c in tl.clips] == before
