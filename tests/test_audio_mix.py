"""PR audio, partie mixage : on RENDU de vrais fichiers (ffmpeg) à partir de sources synthétiques et on MESURE le résultat
(gains, fondus, canaux, solo, source maître, dérive, rééchantillonnage, sonie, limiteur). Aucune valeur n'est supposée."""

import hashlib
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from kolnoa_editor.timeline import (AddGroup, AddTrack, Angle, Clip, MasterRange, MulticamGroup, Timeline, TimelineError,
                                    add_crossfade, assign_master, build_mix_plan, execute, measure_loudness, render_mix,
                                    set_audio_settings, set_clip_gain, set_fades, set_strip, set_track_audio, to_dual_mono, to_mono)

SR = 48000


# --- fabrication et lecture de sons -----------------------------------------------------------------
def write_wav(path: Path, data: np.ndarray, sr: int = SR) -> Path:
    x = np.atleast_2d(data) if data.ndim > 1 else data[:, None]
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(pcm.shape[1]); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def tone(freq, dur, amp=0.5, sr=SR):
    t = np.arange(int(dur * sr)) / sr
    return amp * np.sin(2 * np.pi * freq * t)


def clicks(times, dur, amp, sr=SR):
    x = np.zeros(int(dur * sr))
    for t in times:
        i = int(round(t * sr))
        if 0 <= i < len(x):
            x[i:i + 20] = amp
    return x


def read(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)


def rms_db(x):
    r = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))
    return -200.0 if r < 1e-9 else 20 * np.log10(r)


def seg(x, a, b):
    return x[int(a * SR):int(b * SR)]


def dominant(x):
    f = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.argmax(f) * SR / len(x))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- timelines de test --------------------------------------------------------------------------------
def tl_with(*clips, tracks=("A1",)):
    tl = Timeline(title="T", fps=25)
    from kolnoa_editor.timeline import Track
    for t in tracks:
        tl.tracks.append(Track(t, "audio" if t.startswith("A") else "video"))
    tl.clips += list(clips)
    tl.validate()
    return tl


def render(tl, root, name="out.wav", **kw):
    out = root / name
    rep = render_mix(tl, root, out, **kw)
    return read(out), rep


# --- niveaux, position, fondus --------------------------------------------------------------------------
def test_gains_add_up_and_position_is_exact(tmp_path):
    write_wav(tmp_path / "t.wav", tone(440, 4.0))
    c = Clip("a001", "A1", "t.wav", 25, 50, 1.0)                        # posé à 1 s, 2 s de long, source à partir de 1 s
    tl = tl_with(c)
    ref, _ = render(tl, tmp_path, "ref.wav", duration=4.0)
    assert rms_db(seg(ref, 0, 0.99)) < -90 and rms_db(seg(ref, 3.01, 3.5)) < -90   # silence avant et après
    assert abs(rms_db(seg(ref, 1.1, 2.9)) - rms_db(tone(440, 1.0, 0.5))) < 0.1      # gain unitaire : même niveau (mono → 2 côtés)
    execute(tl, set_clip_gain(tl, "a001", -6.0))
    execute(tl, set_track_audio(tl, "A1", gain_db=-3.0))
    tl.source_gain_db = -1.0
    out, _ = render(tl, tmp_path, "g.wav")
    assert abs(rms_db(seg(out, 1.1, 2.9)) - rms_db(seg(ref, 1.1, 2.9)) - (-10.0)) < 0.1
    assert abs(len(out) / SR - 3.0) < 0.002


def test_fades_follow_their_curve(tmp_path):
    write_wav(tmp_path / "t.wav", tone(440, 6.0, 0.8))
    tl = tl_with(Clip("a001", "A1", "t.wav", 0, 100, 0.0))
    execute(tl, set_fades(tl, "a001", fade_in=1.0, fade_out=1.0, in_curve="linear", out_curve="equal_power"))
    out, _ = render(tl, tmp_path)
    full = rms_db(seg(out, 1.5, 2.5))
    assert abs(rms_db(seg(out, 0.45, 0.55)) - (full - 6.0)) < 0.6                  # linéaire : mi-fondu ≈ −6 dB
    assert rms_db(seg(out, 0.0, 0.05)) < full - 25                                  # départ presque silencieux
    assert abs(rms_db(seg(out, 3.45, 3.55)) - (full - 3.0)) < 0.7                   # puissance constante : mi-fondu ≈ −3 dB
    assert rms_db(seg(out, 3.95, 4.0)) < full - 25


# --- canaux ----------------------------------------------------------------------------------------------
def stereo_file(tmp_path):
    return write_wav(tmp_path / "st.wav", np.stack([tone(440, 3.0), tone(880, 3.0)], axis=1))


def test_default_routing_keeps_stereo_and_mono_goes_to_both_sides(tmp_path):
    stereo_file(tmp_path)
    write_wav(tmp_path / "m.wav", tone(1000, 3.0))
    out, _ = render(tl_with(Clip("a001", "A1", "st.wav", 0, 75, 0.0)), tmp_path)
    assert abs(dominant(seg(out, 0.5, 1.5)[:, 0]) - 440) < 3 and abs(dominant(seg(out, 0.5, 1.5)[:, 1]) - 880) < 3
    out, _ = render(tl_with(Clip("a001", "A1", "m.wav", 0, 75, 0.0)), tmp_path)
    assert np.allclose(out[:, 0], out[:, 1], atol=1e-4)


def test_dual_mono_channels_are_independent(tmp_path):
    stereo_file(tmp_path)
    tl = tl_with(Clip("a001", "A1", "st.wav", 0, 75, 0.0))
    base, _ = render(tl, tmp_path, "b.wav")
    execute(tl, to_dual_mono(tl, "a001", left=0, right=1, channels=2))            # deux micros traités comme deux monos
    execute(tl, set_strip(tl, "a001", 1, gain_db=-6.0))
    out, _ = render(tl, tmp_path, "d.wav")
    assert abs(dominant(seg(out, 0.5, 1.5)[:, 0]) - 440) < 3 and abs(dominant(seg(out, 0.5, 1.5)[:, 1]) - 880) < 3
    assert abs(rms_db(seg(out, 0.5, 2.5)[:, 0]) - rms_db(seg(base, 0.5, 2.5)[:, 0])) < 0.1            # gauche inchangée
    assert abs(rms_db(seg(out, 0.5, 2.5)[:, 1]) - rms_db(seg(base, 0.5, 2.5)[:, 1]) - (-6.0)) < 0.1   # droite −6 dB seule
    execute(tl, set_strip(tl, "a001", 0, mute=True))
    out, _ = render(tl, tmp_path, "m.wav")
    assert rms_db(seg(out, 0.5, 2.5)[:, 0]) < -90 and rms_db(seg(out, 0.5, 2.5)[:, 1]) > -30
    execute(tl, set_strip(tl, "a001", 0, mute=False, solo=True))                 # solo gauche : la droite se tait
    out, _ = render(tl, tmp_path, "s.wav")
    assert rms_db(seg(out, 0.5, 2.5)[:, 1]) < -90 and rms_db(seg(out, 0.5, 2.5)[:, 0]) > -30


def test_mono_split_into_dual_mono_and_pan_law(tmp_path):
    write_wav(tmp_path / "m.wav", tone(1000, 3.0, 0.5))
    tl = tl_with(Clip("a001", "A1", "m.wav", 0, 75, 0.0))
    ref, _ = render(tl, tmp_path, "ref.wav")
    execute(tl, to_mono(tl, "a001"))                                              # un canal, au centre : −3 dB de chaque côté
    out, _ = render(tl, tmp_path, "c.wav")
    assert abs(rms_db(seg(out, 0.5, 2.5)[:, 0]) - (rms_db(seg(ref, 0.5, 2.5)[:, 0]) - 3.0103)) < 0.05
    execute(tl, to_dual_mono(tl, "a001", 0, None, channels=1))                    # deux canaux durs : chacun d'un seul côté
    out, _ = render(tl, tmp_path, "dm.wav")
    assert abs(rms_db(seg(out, 0.5, 2.5)[:, 0]) - rms_db(seg(ref, 0.5, 2.5)[:, 0])) < 0.05
    assert abs(rms_db(seg(out, 0.5, 2.5)[:, 1]) - rms_db(seg(ref, 0.5, 2.5)[:, 1])) < 0.05
    execute(tl, set_strip(tl, "a001", 1, mute=True))                              # on coupe le droit : seul le gauche reste
    out, _ = render(tl, tmp_path, "dm2.wav")
    assert rms_db(seg(out, 0.5, 2.5)[:, 1]) < -90 and rms_db(seg(out, 0.5, 2.5)[:, 0]) > -30


def test_missing_source_channel_is_an_error(tmp_path):
    write_wav(tmp_path / "m.wav", tone(1000, 2.0))
    tl = tl_with(Clip("a001", "A1", "m.wav", 0, 25, 0.0))
    execute(tl, to_dual_mono(tl, "a001", 0, 1))                                    # le modèle ne connaît pas le nombre de canaux…
    with pytest.raises(TimelineError, match="canal source 1"):
        render_mix(tl, tmp_path, tmp_path / "x.wav")                                # … le rendu, si


# --- mute / solo ------------------------------------------------------------------------------------------
def test_mute_and_solo_tracks(tmp_path):
    write_wav(tmp_path / "a.wav", tone(300, 3.0)); write_wav(tmp_path / "b.wav", tone(900, 3.0))
    tl = tl_with(Clip("a001", "A1", "a.wav", 0, 50, 0.0), Clip("b001", "A2", "b.wav", 0, 50, 0.0), tracks=("A1", "A2"))
    out, _ = render(tl, tmp_path)
    f = np.abs(np.fft.rfft(seg(out, 0.5, 1.5)[:, 0]))
    assert f[300] > 100 and f[900] > 100                                            # bin = 1 Hz sur 1 s
    execute(tl, set_track_audio(tl, "A2", muted=True))
    assert build_mix_plan(tl).silent == {"b001": "piste A2 muette"}
    execute(tl, set_track_audio(tl, "A2", muted=False, solo=True))
    plan = build_mix_plan(tl)
    assert plan.silent == {"a001": "une autre piste est en solo"} and [e.clip_id for e in plan.events] == ["b001"]
    out, _ = render(tl, tmp_path, "solo.wav")
    assert abs(dominant(seg(out, 0.5, 1.5)[:, 0]) - 900) < 3


# --- fondu enchaîné -----------------------------------------------------------------------------------------
def test_crossfade_keeps_level_constant(tmp_path):
    write_wav(tmp_path / "t.wav", tone(500, 8.0, 0.5))                              # même matière de part et d'autre
    write_wav(tmp_path / "u.wav", tone(1300, 8.0, 0.5))
    tl = tl_with(Clip("a001", "A1", "t.wav", 0, 75, 1.0), Clip("a002", "A1", "t.wav", 75, 75, 4.0))     # 4 s : suite de 1 s + 3 s
    hard, _ = render(tl, tmp_path, "hard.wav")
    execute(tl, add_crossfade(tl, "a001", "a002", 1.0, curve="linear"))
    out, _ = render(tl, tmp_path, "x.wav")
    ref = rms_db(seg(hard, 1.0, 2.0))
    assert abs(rms_db(seg(out, 2.9, 3.1)) - ref) < 0.3                               # même onde, courbe linéaire : niveau constant
    # deux sources différentes : puissance constante avec equal_power
    tl2 = tl_with(Clip("a001", "A1", "t.wav", 0, 75, 1.0), Clip("a002", "A1", "u.wav", 75, 75, 4.0))
    execute(tl2, add_crossfade(tl2, "a001", "a002", 1.0, curve="equal_power"))
    out2, _ = render(tl2, tmp_path, "x2.wav")
    assert abs(rms_db(seg(out2, 2.9, 3.1)) - ref) < 0.6
    assert abs(len(out2) / SR - 6.5) < 0.05 or len(out2) / SR > 5.9


# --- source maître par séquence, décalage et dérive ---------------------------------------------------------------
def group_tl(tmp_path, rec, a7, offset, drift=0.0, rec_len=12.0):
    """CAM (image, axe du groupe = temps de CAM) ; REC = maître ; A7 = autre prise (offset/dérive)."""
    write_wav(tmp_path / "rec.wav", rec); write_wav(tmp_path / "a7.wav", a7)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=25:duration={rec_len}",
                    "-pix_fmt", "yuv420p", str(tmp_path / "cam.mp4")], check=True)
    from kolnoa_editor.timeline import Track
    tl = Timeline(title="T", fps=25, tracks=[Track("V1", "video")])
    tl.groups.append(MulticamGroup("g001", "REC", angles=[
        Angle("REC", "rec.wav", "audio", True, 0.0, status="reference", confidence=1.0, method="reference"),
        Angle("CAM", "cam.mp4", "video", False, 0.0, status="ok", confidence=0.95, method="audio"),
        Angle("A7", "a7.wav", "audio", True, offset, drift, status="ok", confidence=0.95, method="audio")]))
    tl.clips.append(Clip("s001", "V1", "cam.mp4", 0, int(rec_len * 25), 0.0, audio="group", group="g001", angle="CAM"))
    tl.validate()
    return tl


def click_times(x, amp_min):
    m = np.max(np.abs(x), axis=1)
    idx = np.flatnonzero(m > amp_min)
    return [idx[0] / SR] if len(idx) else []


def test_master_range_switches_source_and_keeps_alignment(tmp_path):
    rec = clicks([2.0, 8.0], 12.0, 0.4)                       # le maître : clics à 2 s et 8 s (axe du groupe)
    a7 = clicks([0.0, 6.0], 12.0, 0.9)                        # A7 : mêmes événements, sa pendule est décalée de 2 s : t_groupe = 2 + t_A7
    tl = group_tl(tmp_path, rec, a7, offset=2.0)
    out, _ = render(tl, tmp_path, "maitre.wav")
    assert len(click_times(seg(out, 1.9, 2.1), 0.3)) == 1 and abs(click_times(seg(out, 1.9, 2.1), 0.3)[0] + 1.9 - 2.0) < 0.002
    assert abs(np.max(np.abs(seg(out, 1.9, 2.1))) - 0.4) < 0.01                       # c'est bien le maître
    execute(tl, assign_master(tl, "g001", 5.0, 12.0, "A7", reason="micro de poche plus proche"))
    out, _ = render(tl, tmp_path, "a7.wav")
    assert abs(np.max(np.abs(seg(out, 1.9, 2.1))) - 0.4) < 0.01                       # avant la plage : toujours le maître
    t = click_times(seg(out, 7.9, 8.1), 0.3)
    assert t and abs(t[0] + 7.9 - 8.0) < 0.002                                       # A7 recalé : même instant que le maître
    assert abs(np.max(np.abs(seg(out, 7.9, 8.1))) - 0.9) < 0.02                       # et c'est bien A7 qu'on entend
    assert build_mix_plan(tl).events[-1].angle == "A7"


def test_drift_is_corrected_over_a_long_take(tmp_path):
    d = 2000e-6                                                # l'horloge de A7 avance de 2000 ppm : 120 ms d'écart après 60 s
    times = [5.0, 30.0, 58.0]
    rec = clicks(times, 62.0, 0.4)
    a7 = clicks([t / (1 + d) for t in times], 62.0 / (1 + d), 0.9)
    tl = group_tl(tmp_path, rec, a7, offset=0.0, drift=d * 1e6, rec_len=62.0)
    execute(tl, assign_master(tl, "g001", 0.0, 62.0, "A7"))
    out, _ = render(tl, tmp_path, "drift.wav")
    for t in times:
        c = click_times(seg(out, t - 0.2, t + 0.2), 0.3)
        assert c and abs(c[0] + t - 0.2 - t) < 0.003, (t, c)                          # recalé à ±3 ms partout
    nodrift = group_tl(tmp_path, rec, a7, offset=0.0, drift=0.0, rec_len=62.0)
    execute(nodrift, assign_master(nodrift, "g001", 0.0, 62.0, "A7"))
    bad, _ = render(nodrift, tmp_path, "nodrift.wav")
    c = click_times(seg(bad, 57.8, 58.2), 0.3)
    assert c and abs(c[0] + 57.8 - 58.0) > 0.05                                       # sans correction : ~116 ms de décalage (le test prouve l'effet)


def test_switch_inside_a_clip_has_no_click(tmp_path):
    rec = tone(300, 12.0, 0.5); a7 = tone(300, 12.0, 0.5)
    tl = group_tl(tmp_path, rec, a7, offset=0.0)
    execute(tl, assign_master(tl, "g001", 5.0, 8.0, "A7"))
    out, _ = render(tl, tmp_path, "sw.wav")
    d = np.abs(np.diff(out[:, 0]))
    assert np.max(d) < 0.1                                                           # aucun saut brutal à la coupure (même onde, fondu de 10 ms)
    plan = build_mix_plan(tl)
    assert [e.angle for e in plan.events] == ["REC", "A7", "REC"]
    assert abs(plan.events[0].fade_out - 0.01) < 1e-9 and plan.events[0].fade_in == 0.0


# --- fréquence d'échantillonnage, sonie, limiteur, sources muettes ----------------------------------------------------
def test_44100_source_is_resampled_not_pitched(tmp_path):
    write_wav(tmp_path / "t.wav", tone(1000, 3.0, 0.5, sr=44100), sr=44100)
    out, rep = render(tl_with(Clip("a001", "A1", "t.wav", 0, 50, 0.0)), tmp_path)
    assert abs(dominant(seg(out, 0.5, 1.5)[:, 0]) - 1000) < 2 and abs(len(out) / SR - 2.0) < 0.003
    assert any("44100 Hz rééchantillonné en 48000 Hz" in w for w in rep.warnings) and rep.sample_rate == 48000


def test_loudness_normalisation_hits_the_target_and_reports_measures(tmp_path):
    write_wav(tmp_path / "t.wav", tone(1000, 8.0, 0.1))
    tl = tl_with(Clip("a001", "A1", "t.wav", 0, 200, 0.0))
    execute(tl, set_audio_settings(tl, target_lufs=-23.0))
    out, rep = render(tl, tmp_path)
    assert abs(rep.after.integrated_lufs - (-23.0)) < 0.3 and rep.before.integrated_lufs != rep.after.integrated_lufs
    assert abs(rep.gain_applied_db - (-23.0 - rep.before.integrated_lufs)) < 1e-3 and not rep.limiter
    assert abs(measure_loudness(tmp_path / "out.wav").integrated_lufs - rep.after.integrated_lufs) < 1e-6   # mesure indépendante identique


def test_limiter_prevents_clipping_and_says_so(tmp_path):
    write_wav(tmp_path / "t.wav", tone(1000, 4.0, 0.5))
    tl = tl_with(Clip("a001", "A1", "t.wav", 0, 100, 0.0))
    execute(tl, set_clip_gain(tl, "a001", 12.0))                                      # crête brute ≈ +6 dBFS
    execute(tl, set_audio_settings(tl, ceiling_dbtp=-1.0))
    out, rep = render(tl, tmp_path)
    assert rep.before.true_peak_dbtp > 3 and rep.limiter
    assert rep.after.true_peak_dbtp <= -0.5 and np.max(np.abs(out)) <= 1.0
    assert any("limiteur intervenu" in w for w in rep.warnings)
    execute(tl, set_clip_gain(tl, "a001", -12.0))
    _, quiet = render(tl, tmp_path, "q.wav")
    assert not quiet.limiter and not any("limiteur" in w for w in quiet.warnings)


def test_silent_camera_is_skipped_with_a_warning_and_missing_media_is_an_error(tmp_path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=3",
                    "-pix_fmt", "yuv420p", str(tmp_path / "mute.mp4")], check=True)
    write_wav(tmp_path / "t.wav", tone(440, 3.0))
    tl = tl_with(Clip("s001", "V1", "mute.mp4", 0, 50, 0.0), Clip("a001", "A1", "t.wav", 0, 50, 0.0), tracks=("V1", "A1"))
    out, rep = render(tl, tmp_path)
    assert any("mute.mp4 n'a pas de piste audio" in w for w in rep.warnings) and rep.events == 1 and rep.after.integrated_lufs is not None
    only = tl_with(Clip("s001", "V1", "mute.mp4", 0, 50, 0.0), tracks=("V1",))
    out, rep = render(only, tmp_path, "silence.wav", duration=2.0)
    assert rms_db(out) < -90 and abs(len(out) / SR - 2.0) < 0.01 and rep.after.integrated_lufs is None
    with pytest.raises(TimelineError, match="introuvable"):
        render_mix(tl_with(Clip("a001", "A1", "nope.wav", 0, 50, 0.0)), tmp_path, tmp_path / "z.wav")


def test_sources_are_never_modified(tmp_path):
    write_wav(tmp_path / "t.wav", tone(440, 3.0))
    before = sha(tmp_path / "t.wav")
    tl = tl_with(Clip("a001", "A1", "t.wav", 0, 50, 0.0))
    execute(tl, set_audio_settings(tl, target_lufs=-20.0))
    render(tl, tmp_path)
    assert sha(tmp_path / "t.wav") == before


def test_audio_falls_back_after_master_ends(tmp_path):
    rec = tone(400, 3.0, 0.25)
    other = tone(900, 7.0, 0.45)
    tl = group_tl(tmp_path, rec, other, offset=1.0, rec_len=7.0)
    out, report = render(tl, tmp_path, "fallback.wav")
    assert np.max(np.abs(seg(out, 1.4, 2.5))) == pytest.approx(.25, abs=.02)
    assert np.max(np.abs(seg(out, 4.0, 5.0))) == pytest.approx(.45, abs=.02)
    assert any("son repris" in warning for warning in report.warnings)
