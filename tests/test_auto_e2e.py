"""Workflow de bout en bout SANS interface : importer → « Monte-moi cette séquence » → consigne ciblée → retouche manuelle → enregistrer → rouvrir."""

import pytest

from kolnoa_editor.app import ProjectController
from kolnoa_editor.auto import (Moment, RushInfo, StyleParams, analyze_silences, parse_directive, plan_sequence, speech_moments)
from kolnoa_editor.auto.directives import DirectiveError
from kolnoa_editor.timeline import Selection, SetClipProps


def rushes():
    mk = lambda m, g, t, dur, moms: RushInfo(m, dur, t, g, "A7IV", True, [Moment(a, b) for a, b in moms])
    return [
        mk("MAIRIE/A7IV/C001.MP4", "MAIRIE", "2026-10-11T10:00:00", 30.0, [(1, 9), (12, 25)]),
        mk("MAIRIE/A7IV/C002.MP4", "MAIRIE", "2026-10-11T10:10:00", 20.0, [(0, 14)]),
        mk("SOIREE/CANON/C003.MP4", "SOIREE", "2026-10-11T20:00:00", 40.0, [(2, 30)]),
        mk("AMBIANCE/DRONE/C004.MP4", "AMBIANCE", "2026-10-11T15:00:00", 12.0, []),
        mk("AMBIANCE/DRONE/C005.MP4", "AMBIANCE", "2026-10-11T15:05:00", 15.0, []),
    ]


@pytest.fixture
def ctl(tmp_path):
    root = tmp_path / "mariage"
    root.mkdir()
    c = ProjectController.open(root, tmp_path / "data")
    c.add_rushes(rushes())
    return c


def test_plan_deterministic_bounded_and_chronological():
    st = StyleParams(shot_min=2, shot_max=5)
    a, b = plan_sequence(rushes(), st), plan_sequence(rushes(), st)
    assert a == b and a
    assert all(2.0 <= s.length <= 6.25 for s in a if s.score >= 1)
    assert all(s.src_in + s.length <= next(r.duration for r in rushes() if r.media == s.media) + 1e-9 for s in a)
    order = [next(r.start_time for r in rushes() if r.media == s.media) for s in a]
    assert order == sorted(order)
    assert any(s.score < 1 for s in a)                       # les rushes sans parole apportent un plan d'ambiance


def test_plan_target_duration():
    shots = plan_sequence(rushes(), StyleParams(target_s=30))
    assert 0 < sum(s.length for s in shots) <= 30 + 1e-9


def test_speech_moments_and_silence_parser():
    assert speech_moments(10, [(2, 4), (6, 7)], 0.8) == [Moment(0.0, 2.0), Moment(4.0, 6.0), Moment(7.0, 10.0)]
    class P:
        stderr = "silence_start: 1.5\nsilence_end: 3.0 | silence_duration: 1.5\nsilence_start: 8.0\n"
    assert analyze_silences("x.mp4", 10.0, runner=lambda *a, **k: P) == [(1.5, 3.0), (8.0, 10.0)]


def test_build_first_edit_is_one_undoable_gesture(ctl):
    base = ctl.tl.canonical()
    seen = []
    rep = ctl.build("Monte-moi cette séquence", analyzer=lambda r, p: r.moments, on_section=lambda n, a, b: seen.append(n))
    assert rep.shots > 3 and seen == ["AMBIANCE", "MAIRIE", "SOIREE"] or sorted(seen) == ["AMBIANCE", "MAIRIE", "SOIREE"]
    v = ctl.tl.clips_on("V1")
    assert all(a.end == b.start for a, b in zip(v, v[1:]))                      # continu, sans trou
    assert len(ctl.tl.clips_on("A1")) == len(v)
    assert all(ctl.session.state_of(c.id) == "auto" for c in ctl.tl.clips)
    ctl.undo()
    assert not ctl.tl.clips and ctl.tl.canonical()["clips"] == base["clips"]
    ctl.session.redo()
    assert ctl.tl.clips


def test_save_reopen_keeps_everything(ctl, tmp_path):
    ctl.build(analyzer=lambda r, p: r.moments)
    snap = ctl.save("v1")
    assert snap.exists()
    before, prov = ctl.tl.canonical(), ctl.session.provenance()
    again = ProjectController.open(tmp_path / "mariage", tmp_path / "data")
    assert again.tl.canonical() == before and again.session.provenance() == prov


def test_shorten_zone_touches_nothing_outside(ctl):
    ctl.build(analyzer=lambda r, p: r.moments)
    tl = ctl.tl
    v = tl.clips_on("V1")
    zone = v[2:5]
    outside_before = {c.id: (c.duration, c.src_in) for c in v if c not in zone}
    s0, total = zone[0].start, sum(c.duration for c in zone)
    r = ctl.command("raccourcis cette partie de 3 secondes", Selection.of_clips(tl, [c.id for c in zone]))
    assert r.kind == "shorten" and r.frames_delta <= -3 * tl.fps + 1
    v2 = tl.clips_on("V1")
    assert all(a.end == b.start for a, b in zip(v2, v2[1:]))
    assert {c.id: (c.duration, c.src_in) for c in v2 if c.id in outside_before} == outside_before     # le reste intact (hors décalage)
    assert sum(c.duration for c in v2 if c.id in {z.id for z in zone}) == total + r.frames_delta
    ctl.undo()
    assert [c.duration for c in tl.clips_on("V1")] == [c.duration for c in v]


def test_validated_clip_blocks_directive_and_manual_edit_is_kept(ctl):
    ctl.build(analyzer=lambda r, p: r.moments)
    tl = ctl.tl
    v = tl.clips_on("V1")
    ctl.session.do(SetClipProps(v[1].id, {"note": "à garder"}))              # retouche manuelle
    ctl.session.validate_clips([v[3].id])
    with pytest.raises(DirectiveError):
        ctl.command("raccourcis de moitié", Selection.of_clips(tl, [v[2].id, v[3].id]))
    assert ctl.tl.clip(v[1].id).note == "à garder"
    assert ctl.session.state_of(v[1].id) == "manuel"
    # une zone sans plan validé peut être raccourcie ; la retouche manuelle voisine reste
    ctl.command("raccourcis de moitié", Selection.of_clips(ctl.tl, [v[4].id, v[5].id]))
    assert ctl.tl.clip(v[1].id).note == "à garder"


def test_faster_and_cutaway_on_upper_track(ctl):
    ctl.build(analyzer=lambda r, p: r.moments, group="MAIRIE")
    tl = ctl.tl
    v = tl.clips_on("V1")
    sel = Selection.of_clips(tl, [v[0].id, v[1].id])
    r = ctl.command("accélère le rythme x2", sel)
    assert r.kind == "faster" and r.frames_delta < 0
    sel = Selection.of_clips(ctl.tl, [c.id for c in ctl.tl.clips_on("V1")[:2]])
    r = ctl.command("remplace par un plan de coupe", sel)
    assert r.changed == 2 and ctl.tl.clips_on("V2")
    c2 = ctl.tl.clips_on("V2")[0]
    assert any(c.start <= c2.start < c.end for c in ctl.tl.clips_on("V1"))          # le plan de coupe recouvre bien un plan de V1
    assert not any(ctl.tl.clip(c.id).media == "MAIRIE/A7IV/C001.MP4" for c in ctl.tl.clips_on("V2"))


def test_stale_selection_refused(ctl):
    ctl.build(analyzer=lambda r, p: r.moments)
    v = ctl.tl.clips_on("V1")
    sel = Selection.of_clips(ctl.tl, [v[0].id])
    ctl.session.do(SetClipProps(v[5].id, {"note": "x"}))
    with pytest.raises(Exception):
        ctl.command("raccourcis", sel)


def test_parse_directive():
    assert parse_directive("Monte-moi la Houppa en 3 minutes").target_s == 180
    assert parse_directive("raccourcis cette partie à 20 s").target_s == 20
    assert parse_directive("raccourcis de 40%").ratio == 0.4
    assert parse_directive("accélère le rythme").ratio == 1.5
    assert parse_directive("mets un plan de coupe").kind == "cutaway"
    with pytest.raises(DirectiveError):
        parse_directive("fais-moi un café")


def test_real_ffmpeg_media_end_to_end(tmp_path):
    """Vrais fichiers (générés par ffmpeg) : import par le scanner, analyse silencedetect réelle, montage, rouverture."""
    import shutil
    import subprocess
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg absent")
    root = tmp_path / "mariage"
    (root / "MAIRIE" / "A7IV").mkdir(parents=True)
    f = root / "MAIRIE" / "A7IV" / "C0001.MP4"
    # 8 s : son 0–3 s, silence 3–5 s, son 5–8 s
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-af", "volume=enable='between(t,3,5)':volume=0",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(f)], check=True)
    c = ProjectController.open(root, tmp_path / "data")
    assert c.import_rushes() == 1
    c.analyze()
    r = next(iter(c.rushes.values()))
    assert len(r.moments) == 2 and abs(r.moments[0].end - 3.0) < 0.3 and abs(r.moments[1].start - 5.0) < 0.3
    c.build("Monte-moi cette séquence", style=StyleParams(shot_min=1, shot_max=2.5))
    assert c.tl.clips_on("V1") and all(x.media == "MAIRIE/A7IV/C0001.MP4" for x in c.tl.clips)
    assert ProjectController.open(root, tmp_path / "data").tl.canonical() == c.tl.canonical()
