"""Mesure de qualité sonore et proposition de la meilleure source : critères mesurés, explicables, jamais appliqués."""

import numpy as np
import pytest

from kolnoa_editor.timeline import (Angle, MulticamGroup, Timeline, TimelineError, assign_master, execute, measure_audio,
                                    propose_master)
from tests.test_audio_mix import SR, tone, write_wav

RNG = np.random.default_rng(7)


def speechlike(dur, floor_db, burst_db, sr=SR):
    """Bruit de fond constant + salves de « parole » (bruit filtré) 0,4 s toutes les 1 s : sert de source vivante."""
    n = int(dur * sr)
    x = RNG.standard_normal(n) * 10 ** (floor_db / 20)
    env = np.zeros(n)
    for t in np.arange(0.3, dur - 0.5, 1.0):
        i = int(t * sr)
        env[i:i + int(0.4 * sr)] = 1.0
    burst = np.convolve(RNG.standard_normal(n), np.ones(8) / 8, mode="same")
    burst /= np.sqrt(np.mean(burst ** 2))
    return np.clip(x + env * burst * 10 ** (burst_db / 20), -1.0, 1.0)


def group(tmp_path, **sources):
    tl = Timeline(title="T", fps=25)
    angles = [Angle("REC", "REC.wav", "audio", True, 0.0, status="reference", confidence=1.0, method="reference")]
    for name, data in sources.items():
        write_wav(tmp_path / f"{name}.wav", data)
        angles.append(Angle(name, f"{name}.wav", "audio", True, 0.0, status="ok", confidence=0.9, method="audio"))
    write_wav(tmp_path / "REC.wav", speechlike(8.0, -66.0, -22.0))
    tl.groups.append(MulticamGroup("g001", "REC", angles=angles))
    tl.validate()
    return tl


def test_measure_reflects_the_signal(tmp_path):
    write_wav(tmp_path / "clean.wav", speechlike(8.0, -68.0, -20.0))
    write_wav(tmp_path / "noisy.wav", speechlike(8.0, -42.0, -20.0))
    write_wav(tmp_path / "clip.wav", np.clip(speechlike(8.0, -68.0, -20.0) * 40.0, -1, 1))
    write_wav(tmp_path / "empty.wav", np.zeros(8 * SR))
    c, n, k, e = (measure_audio(tmp_path / f"{x}.wav") for x in ("clean", "noisy", "clip", "empty"))
    assert c.noise_floor_dbfs < -60 and n.noise_floor_dbfs > -50 and n.noise_floor_dbfs - c.noise_floor_dbfs > 15
    assert c.contrast_db > 30 > n.contrast_db
    assert c.clip_ratio == 0.0 and k.clip_ratio > 0.01
    assert e.silence_ratio == 1.0 and e.integrated_lufs is None and e.peak_dbfs is None
    assert c.channels == 1 and c.channel_correlation is None and abs(c.duration_s - 8.0) < 0.01


def test_measure_window_and_phase(tmp_path):
    x = np.concatenate([speechlike(3.0, -68.0, -20.0), np.zeros(3 * SR), speechlike(2.0, -68.0, -20.0)])
    write_wav(tmp_path / "w.wav", x)
    assert measure_audio(tmp_path / "w.wav", 0.0, 3.0).silence_ratio < 0.3
    assert measure_audio(tmp_path / "w.wav", 3.0, 3.0).silence_ratio == 1.0                   # la fenêtre du milieu est vide
    s = tone(440, 2.0)
    write_wav(tmp_path / "ph.wav", np.stack([s, -s], axis=1))                                 # canaux en opposition de phase
    write_wav(tmp_path / "ok.wav", np.stack([s, s], axis=1))
    assert measure_audio(tmp_path / "ph.wav").channel_correlation < -0.99 and measure_audio(tmp_path / "ok.wav").channel_correlation > 0.99
    with pytest.raises(TimelineError):
        measure_audio(tmp_path / "absent.wav")


def test_proposal_ranks_with_explicit_criteria_and_never_applies(tmp_path):
    tl = group(tmp_path, LAV=speechlike(8.0, -66.0, -20.0), NOISY=speechlike(8.0, -42.0, -20.0),
               CLIP=np.clip(speechlike(8.0, -66.0, -20.0) * 40.0, -1, 1))
    before = tl.canonical()
    p = propose_master(tl, "g001", 0.0, 8.0, tmp_path)
    assert p.best in ("LAV", "REC") and p.ranked[-1].angle == "CLIP"                         # le saturé est dernier
    by = {r.angle: r for r in p.ranked}
    assert by["LAV"].score > by["NOISY"].score > by["CLIP"].score
    assert any("écrêtage" in f for f in by["CLIP"].flags)
    assert {c.name for c in by["LAV"].criteria} >= {"saturation", "bruit_de_fond", "contraste", "silence", "niveau"}
    assert all(c.note for r in p.ranked for c in r.criteria)                                   # chaque critère s'explique
    text = "\n".join(p.explain())
    assert "intelligibilité" in text and "dBFS" in text
    assert tl.canonical() == before and tl.masters == []                                       # une proposition ne modifie rien
    execute(tl, assign_master(tl, "g001", 0.0, 8.0, p.best, reason=p.ranked[0].reason()))      # l'humain valide : c'est lui qui applique
    assert tl.masters[0].reason.startswith("proposition validée")


def test_unsynced_sources_are_excluded_not_ranked_even_if_perfect(tmp_path):
    tl = group(tmp_path, BEST=speechlike(8.0, -75.0, -18.0), OK=speechlike(8.0, -55.0, -20.0))
    tl.group("g001").angle("BEST").status = "incertain"
    p = propose_master(tl, "g001", 0.0, 8.0, tmp_path)
    assert "BEST" not in {r.angle for r in p.ranked} and p.excluded[0][0] == "BEST" and "synchro" in p.excluded[0][1]
    tl.group("g001").angle("OK").status = "impossible"
    tl.group("g001").angle("REC").status = "unsynced"
    none = propose_master(tl, "g001", 0.0, 8.0, tmp_path)
    assert none.best is None and none.margin is None and "aucune source" in none.caveats[0]


def test_close_scores_ask_for_a_listening_decision(tmp_path):
    same = speechlike(8.0, -66.0, -22.0)
    tl = group(tmp_path, TWIN=same.copy())
    write_wav(tmp_path / "REC.wav", same)
    p = propose_master(tl, "g001", 0.0, 8.0, tmp_path)
    assert p.margin is not None and p.margin < 0.08 and any("à écouter" in line for line in p.explain())


def test_sources_filter_and_unmeasurable_source(tmp_path):
    tl = group(tmp_path, A=speechlike(8.0, -60.0, -20.0))
    only = propose_master(tl, "g001", 0.0, 8.0, tmp_path, sources=["A"])
    assert [r.angle for r in only.ranked] == ["A"]
    tl.group("g001").angle("A").media = "absent.wav"
    p = propose_master(tl, "g001", 0.0, 8.0, tmp_path)
    assert [a for a, _ in p.excluded] == ["A"] and "mesure impossible" in p.excluded[0][1]
    with pytest.raises(TimelineError):
        propose_master(tl, "g001", 5.0, 5.0, tmp_path)
