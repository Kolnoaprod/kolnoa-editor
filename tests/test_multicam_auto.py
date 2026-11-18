"""Multicam automatique sur de vrais fichiers : deux caméras qui filment le même événement (son commun, départs décalés) →
regroupées, synchronisées par l'audio, montées avec changement d'angle automatique ; un rush isolé reste à plat ; un seul geste d'annulation."""

import os
import shutil
import subprocess
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from kolnoa_editor.app import ProjectController
from kolnoa_editor.auto import Moment, RushInfo, StyleParams
from kolnoa_editor.auto.multicam import choose_angles, propose_groups
from kolnoa_editor.auto.visual import VisualParams
from kolnoa_editor.probe import probe

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg absent")
SR = 16000


def event_audio(seconds=24, seed=3):
    """Paroles simulées par le générateur de référence des tests de synchro (enveloppe structurée + claquements)."""
    from kolnoa_editor.sync.synth import speech_like
    x = speech_like(seconds, seed, SR).astype(np.float32)
    return x / max(1e-9, float(np.abs(x).max())) * 0.8


def make_cam(path, audio, t_from, t_to, color, vf=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    seg = audio[int(t_from * SR):int(t_to * SR)]
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(seg, -1, 1) * 32767).astype("<i2").tobytes())
    src = f"color=c={color}:s=320x180:r=25:d={t_to - t_from}" if color else f"testsrc=s=320x180:r=25:d={t_to - t_from}"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src, "-i", str(wav)]
    if vf:
        cmd += ["-vf", vf]
    subprocess.run(cmd + ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    wav.unlink()


def info(media, start, dur, group, device, moms=None):
    return RushInfo(media, dur, start, group, device, True, moms or [])


def test_propose_groups_by_overlap_only():
    rs = [info("S/A/1.mp4", "2026-10-11T10:00:00", 20, "S", "A7IV"), info("S/B/2.mp4", "2026-10-11T10:00:05", 20, "S", "CANON"),
          info("S/A/3.mp4", "2026-10-11T10:30:00", 20, "S", "A7IV"),                       # isolé
          info("T/A/4.mp4", "2026-10-11T10:00:03", 20, "T", "A7IV")]                        # autre séquence : pas mélangé
    g = propose_groups(rs)
    assert len(g) == 1 and {r.media for r in g[0].members} == {"S/A/1.mp4", "S/B/2.mp4"}
    assert g[0].master in {"S/A/1.mp4", "S/B/2.mp4"} and g[0].hints["S/B/2.mp4"] == 5.0 or g[0].hints[g[0].master] == 0.0
    assert propose_groups(rs, min_overlap=18) == []                                         # recouvrement trop court


def _tl_two(master_dur=30.0):
    from kolnoa_editor.timeline import Timeline
    from kolnoa_editor.timeline.model import Angle, MulticamGroup
    tl = Timeline(title="t", fps=25, groups=[MulticamGroup("g", "A", "g", [
        Angle("A", "a.mp4", offset=0.0, status="reference", method="reference"), Angle("B", "b.mp4", offset=3.0, status="ok", method="audio"),
        Angle("C", "c.mp4", offset=0.0, status="unsynced")])])
    return tl, {"a.mp4": master_dur, "b.mp4": 20.0, "c.mp4": 30.0}


def test_fallback_without_analysis_keeps_master_no_artificial_cuts():
    tl, durs = _tl_two()
    picks = choose_angles(tl, "g", 0, 23, durs, StyleParams(shot_min=2, shot_max=4))
    assert [p.angle for p in picks] == ["A"] and picks[0].g_start == 0 and picks[0].g_end == 23     # un seul plan continu, pas de rotation
    assert "indisponible" in picks[0].why["motif"] and picks[0].why["analyse"] == "repli"
    assert picks == choose_angles(tl, "g", 0, 23, durs, StyleParams(shot_min=2, shot_max=4))         # déterministe


def _samples(tl, per_angle):
    """Échantillons synthétiques : per_angle[angle] = (netteté, luminance) constants, un par seconde de l'angle."""
    from kolnoa_editor.auto.visual import Sample
    out = {}
    for a in tl.group("g").angles:
        sh, lum = per_angle[a.id]
        out[a.id] = [Sample(t + 0.5, sh, lum, 0.0, 0.0) for t in range(0, 30)]
    return out


def test_visual_choice_rules():
    tl, durs = _tl_two()
    dur = dict(durs)
    # B nettement meilleur que A (A flou) : B dès qu'il couvre (3 s), A avant ; C (non synchronisé) jamais
    s = _samples(tl, {"A": (10.0, 120), "B": (100.0, 120), "C": (500.0, 120)})
    picks = choose_angles(tl, "g", 0, 23, dur, StyleParams(), None, s, VisualParams(min_dwell_s=2.0))
    assert [p.angle for p in picks] == ["A", "B"] and picks[1].g_start >= 3.0 and "C" not in {p.angle for p in picks}
    assert picks[1].why["scores"]["B"] > picks[1].why["scores"]["A"] and "meilleure" in picks[1].why["motif"]
    # angles équivalents : aucun changement
    s = _samples(tl, {"A": (50.0, 120), "B": (52.0, 125), "C": (50.0, 120)})
    assert [p.angle for p in choose_angles(tl, "g", 3, 23, dur, StyleParams(), None, s, VisualParams())] == ["A"]
    # B très sombre (sous-exposé) : on reste sur le maître, même s'il est un peu moins net
    s = _samples(tl, {"A": (40.0, 120), "B": (60.0, 15), "C": (50.0, 120)})
    assert {p.angle for p in choose_angles(tl, "g", 3, 23, dur, StyleParams(), None, s, VisualParams())} == {"A"}
    # critères éteints : maître
    s = _samples(tl, {"A": (10.0, 120), "B": (100.0, 120), "C": (50.0, 120)})
    assert {p.angle for p in choose_angles(tl, "g", 3, 23, dur, StyleParams(), None, s, VisualParams(enabled=False))} == {"A"}


def test_hysteresis_no_flapping_on_noisy_scores():
    tl, durs = _tl_two()
    import random
    from kolnoa_editor.auto.visual import Sample
    rnd = random.Random(5)
    s = {a.id: [Sample(t + 0.5, 50 + rnd.uniform(-8, 8), 120, 0, 0) for t in range(30)] for a in tl.group("g").angles}
    picks = choose_angles(tl, "g", 3, 23, durs, StyleParams(), None, s, VisualParams())
    assert len(picks) <= 2 and all(p.g_end - p.g_start >= 3.0 for p in picks)                        # bruit de mesure ≠ coupes


def test_min_dwell_absorbs_short_plans():
    tl, durs = _tl_two()
    from kolnoa_editor.auto.visual import Sample
    s = _samples(tl, {"A": (50.0, 120), "B": (50.0, 120), "C": (50.0, 120)})
    s["B"] = [Sample(t + 0.5, 500.0 if 8 <= t + 3 < 10 else 50.0, 120, 0, 0) for t in range(30)]      # B meilleur seulement 2 s
    picks = choose_angles(tl, "g", 3, 23, durs, StyleParams(), None, s, VisualParams(min_dwell_s=4.0, switch_penalty=0.1))
    assert all(p.g_end - p.g_start >= 4.0 for p in picks)


def test_end_to_end_real_audio_sync_and_export(tmp_path):
    root = tmp_path / "m"
    audio = event_audio()
    make_cam(root / "CEREMONIE" / "A7IV" / "C1.MP4", audio, 0, 24, None, "boxblur=12:4")          # démarre à t=0 de l'événement
    make_cam(root / "CEREMONIE" / "CANON" / "C2.MP4", audio, 3, 24, None)          # démarre 3 s plus tard
    make_cam(root / "AMBIANCE" / "DRONE" / "C3.MP4", event_audio(8, 9), 0, 8, "green")
    c = ProjectController.open(root, tmp_path / "d")
    mom = [Moment(0.0, 24.0)]
    c.add_rushes([info("CEREMONIE/A7IV/C1.MP4", "2026-10-11T10:00:00", 24, "CEREMONIE", "A7IV", mom),
                  info("CEREMONIE/CANON/C2.MP4", "2026-10-11T10:00:05", 21, "CEREMONIE", "CANON", [Moment(0.0, 21.0)]),   # horodatage faux de 2 s
                  info("AMBIANCE/DRONE/C3.MP4", "2026-10-11T09:00:00", 8, "AMBIANCE", "DRONE", [Moment(0.0, 8.0)])])
    rep = c.build_with_multicam(style=StyleParams(shot_min=2, shot_max=4), analyzer=lambda r, p: r.moments)
    assert len(rep["groups"]) == 1 and rep["plain"] >= 1
    g = c.tl.groups[0]
    b = next(a for a in g.angles if a.media.endswith("C2.MP4"))
    assert b.usable and abs(b.offset - 3.0) < 0.05, (b.status, b.offset)                       # la synchro audio corrige l'horodatage
    v = c.tl.clips_on("V1")
    mc = [x for x in v if x.group]
    assert mc and {x.angle for x in mc} == {a.id for a in g.angles}                              # A (flou) tant que B n'existe pas, puis B (net)
    assert all(x.extra.get('angle_choice') for x in mc) and rep['groups'][0]['analysis']['frames'] > 10
    assert all(a.end == b2.start for a, b2 in zip(v, v[1:]))
    assert v[0].media.startswith("AMBIANCE")                                                      # ordre chronologique : le drone (09:00) d'abord
    out = tmp_path / "film.mp4"
    c.export(out)
    i = probe(out)
    assert abs(i.duration - v[-1].end / c.tl.fps) < 0.2 and i.has_audio
    c.undo()                                                                                      # UN geste : tout le montage et le groupe disparaissent
    assert not c.tl.clips and not c.tl.groups
    assert ProjectController.open(root, tmp_path / "d").tl.canonical() == c.tl.canonical()


def test_unsyncable_angle_is_reported_and_never_used(tmp_path):
    root = tmp_path / "m"
    make_cam(root / "S" / "A" / "1.MP4", event_audio(20, 1), 0, 20, "red")
    make_cam(root / "S" / "B" / "2.MP4", event_audio(20, 2), 0, 20, None)                      # AUTRE son : aucune correspondance
    c = ProjectController.open(root, tmp_path / "d")
    c.add_rushes([info("S/A/1.MP4", "2026-10-11T10:00:00", 20, "S", "A", [Moment(0, 20)]),
                  info("S/B/2.MP4", "2026-10-11T10:00:00", 20, "S", "B", [Moment(0, 20)])])
    rep = c.build_with_multicam(style=StyleParams(shot_min=2, shot_max=4), analyzer=lambda r, p: r.moments)
    assert rep["groups"][0]["skipped"]                                                           # angle non fiable signalé
    used = {x.angle for x in c.tl.clips if x.group}
    assert used and not (used & set(rep["groups"][0]["skipped"]))
