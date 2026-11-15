"""Synchro visuelle EXPÉRIMENTALE (drone muet) : propositions avec confiance explicite, jamais ok, jamais appliquées."""

import pytest

from kolnoa_editor.sync.config import SyncConfig
from kolnoa_editor.sync.synth import luma_scene, write_gray_video
from kolnoa_editor.sync.visual import LumaSeries, accept_candidate, luma_series, propose_visual_sync
from kolnoa_editor.timeline import AddGroup, accept_angle, execute, from_decision_list, TimelineError
from kolnoa_editor.decision_list import DecisionList, Segment

FPS = 10.0
SHARED = [20.0, 55.0, 90.0, 140.0]           # événements communs sur l'axe du groupe


def pair(offset, seed=0, shared=SHARED, private_ref=3, private_sig=4, length=200.0, sig_len=120.0):
    """Référence (durée `length`) et drone (démarrant à `offset` sur l'axe du groupe)."""
    ref = luma_scene(length, FPS, 100 + seed, events=shared, private_events=private_ref)
    ev_sig = [e - offset for e in shared if 3 < e - offset < sig_len - 3]
    sig = luma_scene(sig_len, FPS, 200 + seed, events=ev_sig, private_events=private_sig)
    return LumaSeries(ref, FPS), LumaSeries(sig, FPS)


@pytest.mark.parametrize("offset", [15.0, 40.0])
def test_shared_light_events_give_a_ranked_proposal_close_to_the_truth(offset):
    ref, sig = pair(offset)
    res = propose_visual_sync(ref, sig)
    assert res.status in ("proposition", "faible") and res.experimental     # jamais « ok » ; faible = à vérifier
    best = res.candidates[0]
    assert abs(best.offset - offset) <= 0.2 and best.matched_events >= 2
    assert 0.0 < best.confidence <= SyncConfig().visual_max_confidence       # jamais « ok », plafonnée


def test_only_two_shared_events_overlap_is_honestly_not_demonstrated():
    """À 63,3 s de décalage, seuls 2 événements communs se recouvrent. Depuis la déduplication des événements (un flash
    n'en compte plus plusieurs), le moteur ne prétend plus démontrer la correspondance : « impossible » à confiance
    minimale, tout en gardant le bon décalage en tête de liste pour l'alignement manuel."""
    ref, sig = pair(63.3)
    res = propose_visual_sync(ref, sig)
    assert res.status in ("impossible", "faible")
    best = res.candidates[0]
    assert abs(best.offset - 63.3) <= 0.2
    assert best.confidence <= 0.25


def test_confidence_cap_is_a_setting():
    ref, sig = pair(40.0)
    low = propose_visual_sync(ref, sig, SyncConfig(visual_max_confidence=0.3))
    assert max(c.confidence for c in low.candidates) <= 0.3


def test_views_with_no_common_event_are_declared_impossible_or_low_confidence():
    statuses = []
    for seed in range(200):
        ref = LumaSeries(luma_scene(200, FPS, 300 + seed, events=[30, 80, 150], private_events=2), FPS)
        sig = LumaSeries(luma_scene(120, FPS, 400 + seed, events=[10, 70], private_events=2), FPS)
        res = propose_visual_sync(ref, sig)
        statuses.append(res.status)
        assert all(c.confidence <= 0.3 for c in res.candidates) or res.status == "proposition"
    # taux mesuré sur 500 tirages (bench) : ~0,6 % de fausses « propositions », ~14 % d'« indices faibles »
    assert statuses.count("proposition") <= 3 and statuses.count("impossible") >= 150


def test_too_few_events_is_impossible_with_a_clear_reason():
    ref = LumaSeries(luma_scene(200, FPS, 1, events=[50.0]), FPS)
    sig = LumaSeries(luma_scene(100, FPS, 2, events=[]), FPS)
    res = propose_visual_sync(ref, sig)
    assert res.status == "impossible" and "alignement manuel" in " ".join(res.notes)


def test_repetitive_flashes_are_ambiguous_not_confident():
    strobe = [20.0 + 10.0 * k for k in range(14)]
    ref = LumaSeries(luma_scene(200, FPS, 7, events=strobe), FPS)
    sig = LumaSeries(luma_scene(100, FPS, 8, events=[e - 30.0 for e in strobe if 33 < e < 127]), FPS)
    res = propose_visual_sync(ref, sig)
    assert res.status in ("ambigu", "impossible", "faible")
    assert all(c.confidence <= 0.3 for c in res.candidates)
    if res.status == "ambigu":
        assert len(res.candidates) >= 2


def test_metadata_hint_narrows_the_search():
    ref, sig = pair(63.3)
    hinted = propose_visual_sync(ref, sig, hint=(60.0, 10.0))
    assert abs(hinted.candidates[0].offset - 63.3) <= 0.2
    far = propose_visual_sync(ref, sig, hint=(120.0, 5.0))                  # indice faux : on ne croit pas l'indice
    assert far.status != "proposition" or abs(far.candidates[0].offset - 63.3) > 1.0


def test_proposal_is_never_applied_automatically_and_needs_a_human_to_become_usable():
    ref, sig = pair(40.0)
    cand = propose_visual_sync(ref, sig).candidates[0]
    tl = from_decision_list(DecisionList(title="T", segments=[Segment("s001", "a.mp4", 0, 4)]))
    execute(tl, AddGroup({"id": "g001", "master": "REC", "angles": [
        {"id": "REC", "media": "rec.wav", "kind": "audio", "status": "reference", "method": "reference"},
        {"id": "DRONE", "media": "drone.mp4", "has_audio": False}]}))
    execute(tl, accept_candidate(tl, "g001", "DRONE", cand))
    d = tl.group("g001").angle("DRONE")
    assert d.method == "visual" and d.status == "incertain" and not d.usable and "EXPÉRIMENTAL" in d.note
    execute(tl, accept_angle(tl, "g001", "DRONE"))                         # validation humaine explicite
    assert d.usable and tl.group("g001").angle("DRONE").status == "manual"


def test_end_to_end_with_real_silent_videos(tmp_path):
    """Deux vraies vidéos H.264 (le « drone » est muet), un flash commun : la chaîne ffmpeg → série → proposition."""
    shared = [12.0, 30.0]
    ref_video = write_gray_video(tmp_path / "ref.mp4", luma_scene(60, FPS, 31, events=shared, private_events=1), FPS)
    drone_video = write_gray_video(tmp_path / "drone.mp4", luma_scene(30, FPS, 32, events=[e - 8.0 for e in shared]), FPS)
    ref, sig = luma_series(ref_video), luma_series(drone_video)
    assert abs(ref.duration - 60) < 1 and abs(sig.duration - 30) < 1
    res = propose_visual_sync(ref, sig)
    assert res.candidates and abs(res.candidates[0].offset - 8.0) <= 0.3
    assert res.candidates[0].confidence <= SyncConfig().visual_max_confidence
