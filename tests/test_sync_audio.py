"""Synchronisation audio sur signaux synthétiques : décalages, bruit, silences, échantillonnages, dérive,
clips discontinus, signaux sans correspondance. La vérité terrain est connue ; on mesure les erreurs."""

import time

import numpy as np
import pytest

from kolnoa_editor.sync.audio import estimate_audio_sync
from kolnoa_editor.sync.config import SyncConfig
from kolnoa_editor.sync.sources import ArraySource, FileSource
from kolnoa_editor.sync.synth import extract, speech_like, write_wav

FS = 8000
TOL_S = 0.040          # une image à 25 i/s : l'objectif d'acceptation de la PR B


@pytest.fixture(scope="module")
def master():
    return speech_like(300, seed=5, rate=FS)


@pytest.fixture(scope="module")
def ref(master):
    return ArraySource(master, FS)


def sync(ref, angle, rate=FS, **kw):
    return estimate_audio_sync(ref, ArraySource(angle, rate), **kw)


@pytest.mark.parametrize("offset", [0.0, 12.3456, 100.0, 160.0])
def test_positive_offsets_are_recovered_within_one_frame(master, ref, offset):
    est = sync(ref, extract(master, FS, offset, 100.0, seed=1))
    assert est.status == "ok"
    assert abs(est.offset - offset) < TOL_S and est.confidence >= 0.7


@pytest.mark.parametrize("offset", [-8.0, -25.5])
def test_negative_offsets_camera_started_before_the_recorder(master, ref, offset):
    """La caméra tournait déjà avant l'enregistreur : le début de son clip n'a aucun équivalent dans le maître."""
    est = sync(ref, extract(master, FS, offset, 120.0, seed=2))
    assert est.status in ("ok", "incertain")
    assert abs(est.offset - offset) < TOL_S
    if offset < -20:                                    # la 1re fenêtre est entièrement hors du maître : on le dit
        assert any("sans correspondance" in n for n in est.notes)


@pytest.mark.parametrize("snr", [30, 15, 5, 0])
def test_noise_keeps_the_offset_accurate(master, ref, snr):
    est = sync(ref, extract(master, FS, 47.25, 90.0, snr_db=snr, color=True, gain=0.4, seed=3))
    assert est.status in ("ok", "incertain")
    assert abs(est.offset - 47.25) < TOL_S


def test_very_noisy_signal_never_claims_a_wrong_ok(master, ref):
    for seed in range(8):
        est = sync(ref, extract(master, FS, 47.25, 90.0, snr_db=-12, seed=seed))
        if est.status == "ok":
            assert abs(est.offset - 47.25) < TOL_S           # ok ⇒ juste ; sinon incertain/impossible est honnête


def test_long_silences_do_not_break_the_alignment(master, ref):
    est = sync(ref, extract(master, FS, 30.0, 150.0, silence=[(10, 55), (100, 120)], seed=4))
    assert est.status in ("ok", "incertain") and abs(est.offset - 30.0) < TOL_S
    assert est.metrics["silent"] >= 1


@pytest.mark.parametrize("rate", [16000, 22050, 44100, 48000])
def test_different_sampling_rates_through_real_files(tmp_path, master, ref, rate):
    angle = extract(master, FS, 63.2, 80.0, out_rate=rate, snr_db=25, seed=6)
    wav = write_wav(tmp_path / f"cam_{rate}.wav", angle, rate)
    est = estimate_audio_sync(ref, FileSource(wav, FS))
    assert est.status == "ok" and abs(est.offset - 63.2) < TOL_S


@pytest.mark.parametrize("drift", [-1000.0, -300.0, 50.0, 300.0, 1000.0])
def test_clock_drift_is_measured_in_ppm(master, ref, drift):
    est = sync(ref, extract(master, FS, 20.0, 240.0, drift_ppm=drift, snr_db=20, seed=7))
    assert est.status == "ok" and est.drift_measured
    assert abs(est.drift_ppm - drift) < 25                       # ±25 ppm = 6 ms sur 4 min
    assert abs(est.offset - 20.0) < TOL_S


def test_short_clip_reports_that_drift_cannot_be_measured(master, ref):
    est = sync(ref, extract(master, FS, 50.0, 25.0, drift_ppm=500, seed=8))
    assert est.status in ("ok", "incertain") and not est.drift_measured and est.drift_ppm == 0.0
    assert any("dérive non mesurable" in n for n in est.notes)


def test_several_discontinuous_clips_each_get_their_own_offset(master, ref):
    truth = [12.0, 95.5, 201.25]                                  # trois prises de la même caméra
    for k, off in enumerate(truth):
        est = sync(ref, extract(master, FS, off, 60.0, snr_db=15, seed=10 + k))
        assert est.status == "ok" and abs(est.offset - off) < TOL_S


def test_a_clip_with_an_internal_jump_is_flagged_discontinuous_with_both_offsets(master, ref):
    a = extract(master, FS, 20.0, 80.0, seed=11)                  # offset 20
    b = extract(master, FS, 130.0, 80.0, seed=12)                 # offset 130 → saut de 110 s ; mais dans la portée ?
    est = estimate_audio_sync(ref, ArraySource(np.concatenate([a, b]), FS), SyncConfig(wide_search_s=150.0))
    assert est.status == "discontinu" and len(est.segments) == 2
    offs = sorted(s.offset for s in est.segments)
    assert abs(offs[0] - 20.0) < TOL_S and abs(offs[1] - 50.0) < TOL_S   # 130 - 80 s déjà écoulées dans le clip
    assert any("discontinu" in n for n in est.notes)


def test_unrelated_audio_is_impossible_never_ok(ref):
    verdicts = []
    for seed in range(40):
        other = speech_like(90, seed=100 + seed, rate=FS)
        verdicts.append(sync(ref, other).status)
    assert verdicts.count("ok") == 0                              # taux de faux alignements « ok » = 0/40
    assert verdicts.count("impossible") >= 38                     # et on le dit : impossible, pas « incertain »


def test_unrelated_noise_with_same_statistics_is_not_aligned(ref):
    rng = np.random.default_rng(0)
    est = sync(ref, (rng.standard_normal(FS * 60) * 0.3).astype(np.float32))
    assert est.status == "impossible"


def test_silent_or_too_short_angles_are_refused_cleanly(ref):
    assert sync(ref, np.zeros(FS * 30, dtype=np.float32)).status == "impossible"
    assert sync(ref, speech_like(1.0, 3, FS)).status == "impossible"


def test_thresholds_are_configurable_not_hardcoded(master, ref):
    angle = extract(master, FS, 47.25, 90.0, snr_db=0, color=True, gain=0.4, seed=3)
    default = sync(ref, angle)
    strict = sync(ref, angle, cfg=SyncConfig(good_psr=2000.0))     # exigence de netteté beaucoup plus haute
    assert default.status == "ok" and strict.status != "ok"       # mêmes signaux, décision selon les réglages
    assert strict.confidence < default.confidence and strict.offset == pytest.approx(default.offset, abs=0.005)
    with pytest.raises(ValueError):
        SyncConfig.from_dict({"seuil_inconnu": 1})


def test_a_hint_restricts_the_search_but_is_never_the_answer(master, ref):
    angle = extract(master, FS, 140.0, 80.0, seed=13)
    good = sync(ref, angle, hint=(150.0, 30.0))                   # indice approximatif (métadonnées)
    assert good.status == "ok" and abs(good.offset - 140.0) < TOL_S
    wrong = sync(ref, angle, hint=(10.0, 5.0))                    # indice faux : on ne l'avale pas
    assert wrong.status != "ok" or abs(wrong.offset - 140.0) < TOL_S


def test_compute_time_stays_small_for_a_long_recorder(tmp_path):
    long_master = speech_like(1800, seed=21, rate=FS)             # 30 min d'enregistreur
    angle = extract(long_master, FS, 600.0, 600.0, drift_ppm=120, snr_db=15, seed=22)   # clip de 10 min
    t = time.perf_counter()
    est = estimate_audio_sync(ArraySource(long_master, FS), ArraySource(angle, FS))
    elapsed = time.perf_counter() - t
    assert est.status == "ok" and abs(est.offset - 600.0) < TOL_S and abs(est.drift_ppm - 120) < 25
    assert elapsed < 30, f"{elapsed:.1f}s"
