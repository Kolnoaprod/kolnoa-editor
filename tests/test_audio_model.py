"""PR audio, partie modèle : canaux (mono / dual mono / stéréo), gains, fondus, fondus enchaînés, mute/solo, source maître par séquence.
Tout est réversible, atomique, rejouable depuis le journal ; rien n'est lu sur disque."""

import pytest

from kolnoa_editor.decision_list import DecisionList, Segment
from kolnoa_editor.timeline import (AddGroup, AddTrack, AudioStrip, Clip, Session, Timeline, TimelineError, add_crossfade,
                                    assign_master, clear_master, execute, from_decision_list, master_at, op_from_dict,
                                    remove_crossfade, reset_channels, set_audio_settings, set_clip_gain, set_fades,
                                    set_strip, set_track_audio, to_dual_mono, to_mono, to_stereo)
from tests.test_multicam_model import SONY, WAV, group_dict


def base():
    """V1 : s001 [0,100) ; A1 : a001 [0,50) a002 [50,100) (src 5 s et 8 s) ; groupe g001 (REC maître, A7 ok, OSMO incertain)."""
    tl = from_decision_list(DecisionList(title="T", resolution=(640, 360), segments=[Segment("s001", SONY, 0, 4)]))
    execute(tl, AddTrack({"id": "A1", "kind": "audio", "role": "voix"}))
    execute(tl, AddGroup(group_dict()))
    tl.clips += [Clip("a001", "A1", WAV, 0, 50, 5.0), Clip("a002", "A1", WAV, 50, 50, 8.0)]
    tl.group("g001").angle("A7").has_audio = True
    tl.validate()
    return tl


def roundtrip(tl, op):
    """Annule exactement, rétablit exactement, rejoue la forme journalisée sur une autre copie."""
    work, fresh = tl.copy(), tl.copy()
    before = work.canonical()
    inv = execute(work, op)
    after = work.canonical()
    execute(work, inv)
    assert work.canonical() == before
    execute(work, op_from_dict(op.to_dict()))
    assert work.canonical() == after
    execute(fresh, op_from_dict(op.to_dict()))
    assert fresh.canonical() == after
    return after


# --- gains, fondus, pistes -------------------------------------------------------------------------
def test_gain_fades_and_track_audio_are_reversible_and_bounded():
    tl = base()
    roundtrip(tl, set_clip_gain(tl, "a001", -6.0))
    roundtrip(tl, set_fades(tl, "a001", fade_in=0.5, fade_out=0.25, out_curve="equal_power"))
    roundtrip(tl, set_track_audio(tl, "A1", gain_db=-3.0, muted=False, solo=True))
    for bad in (lambda: set_clip_gain(tl, "a001", 40.0), lambda: set_clip_gain(tl, "a001", -200.0),
                lambda: set_fades(tl, "a001", fade_in=1.5, fade_out=1.0),            # 2,5 s dans un plan de 2 s
                lambda: set_fades(tl, "a001", in_curve="zigzag"), lambda: set_fades(tl, "a001"),
                lambda: set_track_audio(tl, "A1"), lambda: set_track_audio(tl, "A9", muted=True)):
        with pytest.raises(TimelineError):
            bad()


# --- canaux --------------------------------------------------------------------------------------
def test_dual_mono_is_two_independent_channels_not_a_duplicate():
    tl = base()
    execute(tl, to_dual_mono(tl, "a001", left=0, right=None, channels=1))      # un mono réparti en deux canaux
    g, d = tl.clip("a001").strips
    assert (g.src, g.pan, d.src, d.pan) == (0, -1.0, 0, 1.0)
    execute(tl, set_strip(tl, "a001", 1, gain_db=-9.0, mute=False))             # le canal droit se règle seul
    g, d = tl.clip("a001").strips
    assert g.gain_db == 0.0 and d.gain_db == -9.0
    execute(tl, set_strip(tl, "a001", 0, mute=True))
    assert tl.clip("a001").strips[0].mute and not tl.clip("a001").strips[1].mute


def test_channel_builders_check_source_channels_and_reset():
    tl = base()
    with pytest.raises(TimelineError):
        to_dual_mono(tl, "a001", left=0, right=1, channels=1)                   # un mono n'a pas de canal 1
    with pytest.raises(TimelineError):
        to_stereo(tl, "a001", channels=1)
    with pytest.raises(TimelineError):
        set_strip(tl, "a001", 0, gain_db=-3)                                    # pas de canaux explicites
    after = roundtrip(tl, to_stereo(tl, "a001"))
    assert [c["strips"] for c in after["clips"] if c["id"] == "a001"][0][1]["pan"] == 1.0
    execute(tl, to_mono(tl, "a001", src=0))
    execute(tl, reset_channels(tl, "a001"))
    assert tl.clip("a001").strips is None


def test_invalid_strips_are_refused_and_rolled_back():
    tl = base()
    before = tl.canonical()
    from kolnoa_editor.timeline import SetClipStrips
    for bad in ([{"src": 0, "pan": 2.0}], [{"src": -1}], [{"src": 0, "gain_db": 50.0}], [], [{"src": 0}] * 9, [{"zzz": 1}]):
        with pytest.raises(TimelineError):
            execute(tl, SetClipStrips("a001", bad))
        assert tl.canonical() == before


def test_split_keeps_channels_and_curves():
    from kolnoa_editor.timeline import razor
    tl = base()
    execute(tl, to_dual_mono(tl, "a001", 0, None))
    execute(tl, set_fades(tl, "a001", out_curve="log"))
    execute(tl, razor(tl, 25, ["A1"]))
    left, right = tl.clips_on("A1")[:2]
    assert len(left.strips) == len(right.strips) == 2 and right.fade_out_curve == "log"


# --- fondus enchaînés --------------------------------------------------------------------------
def test_crossfade_rules():
    tl = base()
    execute(tl, add_crossfade(tl, "a001", "a002", 1.0))
    (x,) = tl.crossfades
    assert (x.left, x.right, x.duration, x.curve) == ("a001", "a002", 1.0, "equal_power")
    with pytest.raises(TimelineError):
        add_crossfade(tl, "a001", "a002", 0.5)                                  # déjà un fondu enchaîné ici
    roundtrip(tl, remove_crossfade(tl, "x001"))
    t2 = base()
    for kw in ({"duration": 5.0}, {"duration": 0.0}, {"curve": "zigzag"}):      # plus long que les plans / invalide
        with pytest.raises(TimelineError):
            add_crossfade(t2, "a001", "a002", **{"duration": 0.5, **kw})
    with pytest.raises(TimelineError):
        add_crossfade(t2, "a002", "a001", 0.5)                                  # pas voisins dans ce sens
    with pytest.raises(TimelineError):
        add_crossfade(t2, "a001", "a002", 0.5, source_len={WAV: 5.0 + 2.0 + 0.1})   # plus de matière après a001
    t2.clip("a002").src_in = 0.1
    with pytest.raises(TimelineError):
        add_crossfade(t2, "a001", "a002", 1.0)                                  # a002 n'a pas 0,5 s avant son entrée


def test_crossfade_blocks_edits_that_would_break_it_instead_of_silent_desync():
    from kolnoa_editor.timeline import move_clips, razor
    tl = base()
    execute(tl, add_crossfade(tl, "a001", "a002", 1.0))
    def core():
        d = tl.canonical()
        d.pop("retired")                  # un id défait n'est jamais réutilisé (règle du modèle)
        return d
    before = core()
    with pytest.raises(TimelineError):
        execute(tl, move_clips(tl, "a002", 80, linked=False))
    with pytest.raises(TimelineError):
        execute(tl, razor(tl, 25, ["A1"]))
    assert core() == before
    with pytest.raises(TimelineError):
        set_fades(tl, "a001", fade_out=0.3)                                     # le fondu enchaîné remplace le fondu de sortie
    with pytest.raises(TimelineError):
        set_fades(tl, "a002", fade_in=0.3)


# --- source maître par séquence ------------------------------------------------------------------
def test_master_ranges_overwrite_split_and_fall_back_to_group_master():
    tl = base()
    tl.group("g001").angle("OSMO").status = "ok"
    tl.group("g001").angle("OSMO").has_audio = True
    assert master_at(tl, "g001", 10.0) == "REC"                                 # aucune plage : maître du groupe
    execute(tl, assign_master(tl, "g001", 10.0, 60.0, "A7", reason="micro cravate plus proche"))
    execute(tl, assign_master(tl, "g001", 30.0, 40.0, "OSMO"))                  # recouvre le milieu : A7 est scindé en deux
    got = [(r.start, r.end, r.source) for r in sorted(tl.masters, key=lambda r: r.start)]
    assert got == [(10.0, 30.0, "A7"), (30.0, 40.0, "OSMO"), (40.0, 60.0, "A7")]
    assert len({r.id for r in tl.masters}) == 3
    assert [master_at(tl, "g001", t) for t in (5, 10, 29.9, 30, 39.9, 40, 59.9, 60, 100)] == \
        ["REC", "A7", "A7", "OSMO", "OSMO", "A7", "A7", "REC", "REC"]
    assert tl.masters and next(r for r in tl.masters if r.source == "A7").reason.startswith("micro")
    execute(tl, clear_master(tl, "g001", 20.0, 50.0))
    got = [(r.start, r.end, r.source) for r in sorted(tl.masters, key=lambda r: r.start)]
    assert got == [(10.0, 20.0, "A7"), (50.0, 60.0, "A7")]


def test_master_assignment_is_atomic_reversible_and_journaled():
    tl = base()
    execute(tl, assign_master(tl, "g001", 10.0, 60.0, "A7"))
    roundtrip(tl, assign_master(tl, "g001", 30.0, 40.0, "REC"))
    roundtrip(tl, clear_master(tl, "g001", 0.0, 100.0))


def test_master_refuses_unreliable_silent_or_unknown_sources():
    tl = base()
    with pytest.raises(TimelineError):
        assign_master(tl, "g001", 0, 10, "OSMO")            # « incertain » : à valider d'abord
    with pytest.raises(TimelineError):
        assign_master(tl, "g001", 0, 10, "DRONE")           # pas d'audio
    with pytest.raises(TimelineError):
        assign_master(tl, "g001", 0, 10, "ZZZ")
    with pytest.raises(TimelineError):
        assign_master(tl, "g001", 10, 10, "A7")
    with pytest.raises(TimelineError):
        clear_master(tl, "g001", 0, 10)                     # rien à effacer
    assert tl.masters == []


def test_audio_settings_validate_and_roundtrip():
    tl = base()
    roundtrip(tl, set_audio_settings(tl, sample_rate=44100, ceiling_dbtp=-2.0, target_lufs=-16.0, switch_fade_ms=20.0))
    for bad in ({"sample_rate": 22050}, {"ceiling_dbtp": 3.0}, {"target_lufs": 5.0}, {"switch_fade_ms": -1}):
        with pytest.raises(TimelineError):
            execute(tl, set_audio_settings(tl, **bad))
    with pytest.raises(TimelineError):
        execute(tl, set_audio_settings(tl, volume=3))


def test_everything_survives_session_reopen(tmp_path):
    tl = base()
    s = Session.create(tmp_path / "p", tl)
    s.do(to_dual_mono(s.tl, "a001", 0, None), "dual mono")
    s.do(set_fades(s.tl, "a001", fade_in=0.2, in_curve="equal_power"), "fondu")
    s.do(add_crossfade(s.tl, "a001", "a002", 0.6), "fondu enchaîné")
    s.do(assign_master(s.tl, "g001", 5.0, 25.0, "A7", "test"), "source")
    s.do(set_audio_settings(s.tl, target_lufs=-18.0), "réglages")
    s.undo()
    s.redo()
    again = Session.open(tmp_path / "p")
    assert again.tl.canonical() == s.tl.canonical()
    assert len(again.tl.clip("a001").strips) == 2 and again.tl.crossfades[0].duration == 0.6
    assert again.tl.masters[0].reason == "test" and again.tl.audio_settings.target_lufs == -18.0


def test_old_timelines_without_audio_fields_still_load():
    d = base().to_dict()
    for k in ("masters", "crossfades", "audio_settings"):
        d.pop(k)
    for c in d["clips"]:
        for k in ("strips", "fade_in_curve", "fade_out_curve"):
            c.pop(k)
    for t in d["tracks"]:
        t.pop("solo")
    tl = Timeline.from_dict(d)
    tl.validate()
    assert tl.masters == [] and tl.audio_settings.sample_rate == 48000 and tl.clip("a001").strips is None
