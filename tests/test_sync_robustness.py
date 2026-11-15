"""Revue de la PR #9 : validation stricte des réglages, déduplication des événements visuels, faux pics corrélés
(musique répétitive, applaudissements, écho long, même musique à deux instants)."""

import numpy as np
import pytest
from scipy.signal import fftconvolve

from kolnoa_editor.sync.audio import estimate_audio_sync
from kolnoa_editor.sync.config import SyncConfig
from kolnoa_editor.sync.sources import ArraySource
from kolnoa_editor.sync.synth import extract, speech_like
from kolnoa_editor.sync.visual import LumaSeries, _dedupe, _events, propose_visual_sync

FS = 8000


# --- réglages ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"rate": 0}, {"env_rate": 0}, {"window_s": 0}, {"max_windows": 0}, {"min_windows": 0}, {"visual_top_k": 0},
    {"visual_fps": -1}, {"good_psr": 5.0}, {"min_psr": 25.0}, {"visual_good_psr": 4.0},
    {"ok_confidence": 1.5}, {"uncertain_confidence": -0.1}, {"uncertain_confidence": 0.8}, {"coverage_good": 2},
    {"visual_max_confidence": 3}, {"visual_max_coincidence": 0.5}, {"coarse_ambiguity_ratio": 1.2},
    {"band_hz": [3000, 100]}, {"band_hz": [0, 3000]}, {"band_hz": [100]}, {"band_hz": [100, 5000]},
    {"jump_tol_s": 0.01}, {"refine_s": 50}, {"max_windows": 2.5}, {"rate": True}, {"rate": "8000"},
])
def test_absurd_settings_are_rejected_with_a_readable_error(bad):
    with pytest.raises(ValueError, match="réglages de synchro invalides"):
        SyncConfig.from_dict(bad)


def test_error_message_lists_every_problem_at_once():
    with pytest.raises(ValueError) as e:
        SyncConfig.from_dict({"rate": 0, "window_s": -3, "good_psr": 1.0})
    msg = str(e.value)
    assert "rate" in msg and "window_s" in msg and "good_psr" in msg


def test_direct_construction_and_json_roundtrip_are_validated_too(tmp_path):
    with pytest.raises(ValueError):
        SyncConfig(rate=0)
    p = tmp_path / "sync.json"
    p.write_text('{"visual_top_k": 0}', encoding="utf-8")
    with pytest.raises(ValueError):
        SyncConfig.load(p)
    cfg = SyncConfig.from_dict(SyncConfig().to_dict())          # la valeur par défaut est valide et stable
    assert cfg == SyncConfig()


def test_unknown_keys_still_rejected():
    with pytest.raises(ValueError, match="inconnus"):
        SyncConfig.from_dict({"nope": 1})


# --- événements visuels -----------------------------------------------------------------------------------------
def _scene(n=600, fps=10.0, flashes=(), width=3, level=80.0):
    x = np.full(n, 40.0)
    for f in flashes:
        i = int(f * fps)
        x[i:i + width] += level
    return LumaSeries(x, fps)


def test_a_single_flash_counts_as_one_event():
    cfg = SyncConfig()
    _, idx = _events(_scene(flashes=[20.0], width=6), cfg)
    assert len(idx) == 1, idx


def test_separate_flashes_stay_separate_events():
    cfg = SyncConfig()
    _, idx = _events(_scene(flashes=[10.0, 25.0, 40.0]), cfg)
    assert len(idx) == 3


def test_dedupe_keeps_the_strongest_index_of_each_group():
    z = np.zeros(50)
    z[[10, 11, 12]] = [3, 9, 4]
    z[30] = 5
    assert list(_dedupe(np.array([10, 11, 12, 30]), z, 10)) == [11, 30]
    assert len(_dedupe(np.array([], dtype=int), z, 10)) == 0


def test_periodic_light_sequence_is_never_a_confident_proposal():
    """Une enseigne qui clignote toutes les 5 s : beaucoup de décalages se valent."""
    ref = _scene(n=2000, flashes=np.arange(5, 190, 5.0))
    sig = _scene(n=800, flashes=np.arange(2, 78, 5.0))
    res = propose_visual_sync(ref, sig)
    assert res.status in ("ambigu", "faible", "impossible"), res.status
    assert all(c.confidence <= SyncConfig().visual_max_confidence for c in res.candidates)


def test_single_flash_each_side_is_not_enough():
    res = propose_visual_sync(_scene(flashes=[30.0], width=6), _scene(n=300, flashes=[10.0], width=6))
    assert res.status == "impossible"


# --- faux pics corrélés ---------------------------------------------------------------------------------------
def _applause(seconds, seed):
    r = np.random.default_rng(seed)
    n = int(seconds * FS)
    x = np.zeros(n)
    for t in r.uniform(0, seconds - 3, int(seconds / 7)):
        L = int(r.uniform(1, 3) * FS)
        s = int(t * FS)
        x[s:s + L] += r.standard_normal(L) * np.hanning(L)
    return x


def run(master, angle):
    return estimate_audio_sync(ArraySource(master, FS), ArraySource(angle, FS))


def test_strictly_periodic_loop_is_not_declared_ok_at_a_wrong_offset():
    loop = speech_like(2.0, seed=9, rate=FS)
    master = np.tile(loop, 150) + 0.05 * np.random.default_rng(3).standard_normal(300 * FS)
    est = run(master, extract(master, FS, 40.0, 60.0, seed=2))
    assert est.status != "ok" or abs(est.offset - 40.0) < 0.04
    assert est.status in ("impossible", "incertain")        # période de 2 s : l'instant exact est indéterminable


def test_repetitive_music_over_unique_speech_never_gives_a_confident_wrong_answer():
    loop = np.tile(speech_like(2.0, seed=9, rate=FS), 150)
    master = 0.7 * loop + 0.3 * speech_like(300, seed=11, rate=FS)
    est = run(master, extract(master, FS, 40.0, 60.0, seed=2))
    assert est.status != "ok" or abs(est.offset - 40.0) < 0.04


def test_shared_applause_is_recovered_and_unrelated_applause_is_not_ok():
    a = _applause(300, 1)
    est = run(a, extract(a, FS, 70.0, 60.0, seed=4))
    assert est.status == "ok" and abs(est.offset - 70.0) < 0.04
    assert run(a, _applause(60, 99)).status != "ok"


def test_long_reverberant_echo_keeps_the_offset_within_a_frame_or_is_not_ok():
    m = speech_like(300, seed=5, rate=FS)
    r = np.random.default_rng(1)
    ir = np.exp(-np.arange(int(1.2 * FS)) / (0.3 * FS)) * r.standard_normal(int(1.2 * FS))
    ir[0] = 1.0
    ir /= np.abs(ir).sum() ** 0.5
    angle = fftconvolve(extract(m, FS, 55.0, 60.0, seed=6), ir)[:60 * FS]
    est = run(m, angle)
    assert est.status != "ok" or abs(est.offset - 55.0) < 0.04


def test_same_song_played_twice_is_flagged_ambiguous_not_ok():
    """La chanson passe deux fois dans l'enregistrement maître ; l'angle n'en contient qu'une : deux réponses valent."""
    song = speech_like(40, seed=21, rate=FS)
    master = speech_like(300, seed=31, rate=FS) * 0.5
    master[20 * FS:60 * FS] += song
    master[200 * FS:240 * FS] += song
    angle = speech_like(40, seed=77, rate=FS) * 0.3 + song
    est = run(master, angle)
    assert est.status == "incertain"
    assert any("ambigu" in n for n in est.notes)
    assert est.metrics["rival_peak_ratio"] >= SyncConfig().coarse_ambiguity_ratio


def test_a_metadata_hint_resolves_the_ambiguity_it_is_only_a_hint():
    song = speech_like(40, seed=21, rate=FS)
    master = speech_like(300, seed=31, rate=FS) * 0.5
    master[20 * FS:60 * FS] += song
    master[200 * FS:240 * FS] += song
    angle = speech_like(40, seed=77, rate=FS) * 0.3 + song
    est = estimate_audio_sync(ArraySource(master, FS), ArraySource(angle, FS), hint=(20.0, 30.0))
    assert abs(est.offset - 20.0) < 0.04          # l'indice restreint la recherche ; la mesure reste audio


def test_unambiguous_speech_is_not_flagged_by_the_new_check():
    m = speech_like(300, seed=5, rate=FS)
    est = run(m, extract(m, FS, 90.0, 80.0, seed=3))
    assert est.status == "ok"
    assert est.metrics["rival_peak_ratio"] < SyncConfig().coarse_ambiguity_ratio


# --- conversions, bords, clips sans chevauchement ---------------------------------------------------------------
def test_time_conversions_round_trip_with_drift():
    from kolnoa_editor.timeline.model import Angle
    a = Angle(id="a", media="x.mp4", offset=12.5, drift_ppm=-850.0)
    for t in (0.0, 1.234, 600.0, 3600.0):
        assert abs(a.to_angle(a.to_group(t)) - t) < 1e-9


def test_source_times_at_bounds_are_inclusive_and_outside_is_none():
    from kolnoa_editor.decision_list import DecisionList, Segment
    from kolnoa_editor.sync.manual import source_times_at
    from kolnoa_editor.timeline import AddGroup, execute, from_decision_list
    tl = from_decision_list(DecisionList(title="T", resolution=(640, 360), segments=[Segment("s001", "a.mp4", 0, 4)]))
    execute(tl, AddGroup({"id": "g001", "master": "REC", "angles": [
        {"id": "REC", "media": "rec.wav", "kind": "audio", "status": "reference", "method": "reference", "confidence": 1.0},
        {"id": "A", "media": "a.mp4", "status": "manual", "method": "manual", "confidence": 1.0, "offset": 10.0,
         "range": [5.0, 15.0]}]}))
    assert source_times_at(tl, "g001", 15.0)["A"] == 5.0              # borne basse incluse
    assert source_times_at(tl, "g001", 25.0)["A"] == 15.0             # borne haute incluse
    assert source_times_at(tl, "g001", 25.001)["A"] is None
    assert source_times_at(tl, "g001", 14.999)["A"] is None
    assert source_times_at(tl, "g001", 9.0)["A"] is None              # t_angle négatif


def test_clip_with_nothing_in_common_is_impossible_and_clip_partly_outside_is_never_ok():
    m = speech_like(120, seed=5, rate=FS)
    assert run(m, speech_like(60, seed=44, rate=FS)).status == "impossible"
    inside = extract(m, FS, 80.0, 40.0, seed=1)
    est = run(m, np.concatenate([inside, speech_like(40, seed=45, rate=FS)]))
    assert est.status != "ok" and abs(est.offset - 80.0) < 0.04
    assert any("sans correspondance" in n for n in est.notes)


# --- constats sur de vrais rushes (MAIRIE, 2 micros DJI + caméras Sony) ----------------------------------------
def test_digital_silence_track_is_reported_as_empty_not_as_no_match():
    m = speech_like(120, seed=5, rate=FS)
    est = run(m, np.zeros(30 * FS, dtype=np.float32))
    assert est.status == "impossible"
    assert any("piste audio vide" in n for n in est.notes) and est.metrics.get("empty_audio") is True


def test_isolated_false_peaks_are_ignored_and_do_not_break_the_run_into_segments():
    """Mesuré sur 2 micros réels : des fenêtres isolées donnent un faux pic (PSR 8-20). Elles ne sont pas des
    discontinuités : le décalage dominant reste, avec une note, et la confiance baisse."""
    m = speech_like(400, seed=5, rate=FS)
    angle = extract(m, FS, 20.0, 300.0, seed=1).copy()
    other = speech_like(300, seed=77, rate=FS)
    for a, b in ((48, 74), (125, 150), (201, 226)):   # trois fenêtres d'analyse entières remplacées par un autre contenu
        angle[a * FS:b * FS] = other[a * FS:b * FS]
    est = run(m, angle)
    assert abs(est.offset - 20.0) < 0.04
    assert est.status != "discontinu" and len(est.segments) == 1
    assert est.status in ("ok", "incertain")
    assert est.confidence < 1.0


def test_two_clean_offsets_in_a_row_still_make_a_discontinuity():
    m = speech_like(500, seed=5, rate=FS)
    a = extract(m, FS, 10.0, 120.0, seed=1)
    b = extract(m, FS, 175.0, 120.0, seed=2)               # saut de 45 s (dans la portée de recherche)
    est = run(m, np.concatenate([a, b]))
    assert est.status == "discontinu" and len(est.segments) == 2
    assert abs(est.segments[0].offset - 10.0) < 0.05 and abs(est.segments[1].offset - (175.0 - 120.0)) < 0.05


def test_a_step_of_25_ms_between_two_halves_stays_one_segment_with_a_good_estimate():
    """Pas de 22 ms mesuré dans l'enregistrement d'un micro sans fil : même segment, erreur ≤ 25 ms."""
    m = speech_like(400, seed=5, rate=FS)
    a = extract(m, FS, 50.0, 100.0, seed=1)
    b = extract(m, FS, 150.022, 100.0, seed=2)
    est = run(m, np.concatenate([a, b]))
    assert est.status in ("ok", "incertain") and len(est.segments) == 1
    assert abs(est.offset - 50.0) < 0.03
