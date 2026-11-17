"""A1-3 : propriétés et scénarios reproductibles du résolveur d'ancres contre l'oracle exhaustif.

`KOLNOA_ANCHOR_SCENARIOS` (défaut 10000) règle le nombre de scénarios ; chaque scénario = graine déterministe, 1 à 3 médias,
1 à 3 pistes, 6 à 10 opérations réelles du moteur (rasoir, déplacement, rognage, suppression, suppression avec ripple) et
annulation complète par la pile des inverses. Après CHAQUE opération, chaque ancre est comparée à l'oracle."""
import os
import random
from fractions import Fraction as F

import pytest

from anchors_oracle import point_state, range_state
from kolnoa_editor.anchors import AnchorRefused, MediaRegistry, Resolver, anchor_at, range_between
from kolnoa_editor.timeline import editing
from kolnoa_editor.timeline.model import Clip, Timeline, Track, TimelineError
from kolnoa_editor.timeline.ops import execute, ripple_delete

N = int(os.environ.get("KOLNOA_ANCHOR_SCENARIOS", "10000"))
FPS = (24, 25, 30, 50, 60)


def build(seed):
    r = random.Random(seed)
    fps = r.choice(FPS)
    ntr = r.randint(1, 3)
    tl = Timeline(title="p", fps=fps, tracks=[Track(f"V{i}") for i in range(ntr)])
    reg = MediaRegistry(id_factory=lambda: f"m{len(reg.entries):03d}")
    medias = [f"rush{i}.mov" for i in range(r.randint(1, 3))]
    for m in medias:
        reg.register(m, 100 + len(m), 1, duration_us=600_000_000, fps=f"{fps}/1", quick_hash=m)
    n = 0
    for t in tl.tracks:
        pos = r.randint(0, 20)
        for _ in range(r.randint(1, 4)):
            dur = r.randint(10, 120)
            tl.clips.append(Clip(f"c{n}", t.id, r.choice(medias), pos, dur, src_in=round(r.randint(0, 3000) / fps, 6)))
            n += 1
            pos += dur + r.choice((0, 0, 0, r.randint(1, 30)))
    return r, tl, reg


def make_anchors(r, tl, reg):
    out = []
    for i in range(3):
        c = r.choice(tl.clips)
        try:
            out.append(anchor_at(tl, reg, c.track, r.randrange(c.start, c.end), f"p{i}"))
        except AnchorRefused:
            pass
    for i in range(2):
        c = r.choice(tl.clips)
        if c.duration >= 4:
            a = r.randrange(c.start, c.end - 1)
            try:
                out.append(range_between(tl, reg, c.track, a, r.randrange(a + 1, c.end + 1), f"r{i}"))
            except AnchorRefused:
                pass
    return out


def random_op(r, tl):
    k = r.choice(("razor", "razor", "move", "trim", "delete", "ripple", "delrange"))
    try:
        c = r.choice(tl.clips)
        end = max(x.end for x in tl.clips) + 5
        if k == "razor":
            return editing.razor(tl, r.randrange(0, end))
        if k == "move":
            return editing.move_clips(tl, c.id, r.randrange(0, end + 50), c.track if r.random() < 0.7 else r.choice(tl.tracks).id,
                                      linked=False)
        if k == "trim":
            return editing.trim_edge(tl, c.id, r.choice(("in", "out")), r.randint(-15, 15), linked=False)
        if k == "ripple":
            return ripple_delete(tl, c.id)
        if k == "delrange":
            a = r.randrange(0, end)
            return editing.delete_range(tl, a, a + r.randint(1, 40), ripple=r.random() < 0.5, linked=False)
        return editing.delete_range(tl, c.start, c.end, ripple=False, linked=False)
    except (TimelineError, ValueError, KeyError, IndexError):
        return None


def to_media(c, us):          # axe media : l'instant est déjà celui du média
    return us


EPS = F(1)


def check(tl, reg, anchors, where):
    res = Resolver(tl, reg)
    for a in anchors:
        clips = [c for c in tl.clips if reg.entries[a.key].path_rel == c.media]
        got = res.resolve(a)
        gstate = "exacte" if got.state == "contexte_modifié" else got.state   # le contexte n'est pas géométrique : hors oracle
        if hasattr(a, "us"):
            st, frames = point_state(tl, clips, F(a.us), to_media, EPS)
            assert gstate == st, (where, a, got, st, frames)
            if st == "exacte":
                seg = got.options[0][0]
                assert [(seg.clip_id, seg.frame)] == frames, (where, a, got, frames)
        else:
            st, miss = range_state(tl, clips, F(a.start_us), F(a.end_us), to_media, EPS)
            assert gstate == st, (where, a, got, st, float(miss))


def run(seed):
    r, tl, reg = build(seed)
    anchors = make_anchors(r, tl, reg)
    if not anchors:
        return 0
    check(tl, reg, anchors, f"{seed}/init")
    snapshot = tl.copy()
    inverses = []
    for step in range(r.randint(6, 10)):
        tx = random_op(r, tl)
        if tx is None:
            continue
        try:
            inverses.append(execute(tl, tx))
        except (TimelineError, ValueError):
            continue
        check(tl, reg, anchors, f"{seed}/step{step}")
    for inv in reversed(inverses):                       # annulation complète : retour exact à l'état initial
        execute(tl, inv)
    assert sorted(tl.clips, key=lambda c: c.id) == sorted(snapshot.clips, key=lambda c: c.id), f"{seed}: annulation incomplète"
    check(tl, reg, anchors, f"{seed}/undo")
    return len(anchors)


def test_oracle_scenarios():
    checked = sum(run(s) > 0 for s in range(N))
    assert checked >= N * 0.9          # presque tous les scénarios produisent des ancres


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_scenarios_are_reproducible(seed):
    a, b = build(seed), build(seed)
    assert a[1].clips == b[1].clips


# --- multicam : axe du groupe, décalages et dérives -------------------------------------------------------------------------
from kolnoa_editor.anchors import AXIS_GROUP                    # noqa: E402
from kolnoa_editor.timeline.model import Angle, MulticamGroup   # noqa: E402
from kolnoa_editor.timeline.ops import switch_angle             # noqa: E402

N_MC = int(os.environ.get("KOLNOA_ANCHOR_SCENARIOS_MC", "1500"))


def build_mc(seed):
    r = random.Random(10_000_000 + seed)
    fps = r.choice(FPS)
    angles = [Angle(x, f"cam{x}.mov", offset=round(r.uniform(-3, 3), 3), drift_ppm=float(r.randint(-200, 200)), status="synced")
              for x in "ABC"[:r.randint(2, 3)]]
    g = MulticamGroup("g1", "A", angles=angles)
    tl = Timeline(title="mc", fps=fps, tracks=[Track("V0")], groups=[g])
    reg = MediaRegistry(id_factory=lambda: f"m{len(reg.entries):03d}")
    for a in angles:
        reg.register(a.media, 50 + len(a.media), 1, duration_us=900_000_000, fps=f"{fps}/1", quick_hash=a.media)
    pos = 0
    for i in range(r.randint(1, 4)):
        a = r.choice(angles)
        dur = r.randint(15, 100)
        tl.clips.append(Clip(f"m{i}", "V0", a.media, pos, dur, src_in=round(r.uniform(5, 200), 6), group="g1", angle=a.id,
                             audio="group"))
        pos += dur
    return r, tl, reg, g


def mc_axis(g):
    ang = {a.id: a for a in g.angles}

    def to_axis(c, us_angle):
        a = ang[c.angle]
        return (F(a.offset).limit_denominator(10**6) + (us_angle / F(1_000_000)) * (1 + F(int(a.drift_ppm), 1_000_000))) * 1_000_000
    return to_axis


def run_mc(seed):
    r, tl, reg, g = build_mc(seed)
    anchors = []
    for i in range(3):
        c = r.choice(tl.clips)
        anchors.append(anchor_at(tl, reg, "V0", r.randrange(c.start, c.end), f"p{i}", axis=AXIS_GROUP))
    c = r.choice(tl.clips)
    if c.duration >= 4:
        a0 = r.randrange(c.start, c.end - 1)
        anchors.append(range_between(tl, reg, "V0", a0, r.randrange(a0 + 1, c.end + 1), "r0", axis=AXIS_GROUP))
    to_axis = mc_axis(g)

    def verify(where):
        res = Resolver(tl, reg)
        clips = [c for c in tl.clips if c.group == "g1"]
        for a in anchors:
            got = res.resolve(a)
            gs = "exacte" if got.state == "contexte_modifié" else got.state
            if hasattr(a, "us"):
                st, frames = point_state(tl, clips, F(a.us), to_axis, F(2))
                assert gs == st, (where, a, got, st, frames)
                if st == "exacte":
                    assert [(got.options[0][0].clip_id, got.options[0][0].frame)] == frames, (where, a, got, frames)
            else:
                st, miss = range_state(tl, clips, F(a.start_us), F(a.end_us), to_axis, F(2))
                assert gs == st, (where, a, got, st, float(miss))
    verify(f"mc{seed}/init")
    for step in range(r.randint(3, 8)):
        try:
            k = r.choice(("switch", "switch", "razor", "trim"))
            c = r.choice(tl.clips)
            tx = (switch_angle(tl, c.id, r.choice(g.angles).id) if k == "switch" else
                  editing.razor(tl, r.randrange(0, max(x.end for x in tl.clips))) if k == "razor" else
                  editing.trim_edge(tl, c.id, r.choice(("in", "out")), r.randint(-8, 8), linked=False))
            execute(tl, tx)
        except (TimelineError, ValueError, KeyError):
            continue
        verify(f"mc{seed}/step{step}")


def test_multicam_scenarios_group_axis():
    for s in range(N_MC):
        run_mc(s)


# --- refus explicites : jamais d'ancre approximative -------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["vfr", "ntsc", "speed", "slowmo"])
def test_unsupported_always_refused_or_typed(kind):
    refused = 0
    for s in range(300):
        r, tl, reg = build(20_000_000 + s)
        for e in reg.entries.values():
            if kind == "vfr":
                e.vfr = True
            elif kind == "ntsc":
                e.fps = "30000/1001"
        if kind in ("speed", "slowmo"):
            for c in tl.clips:
                c.extra["speed"] = 2.0 if kind == "speed" else 0.5
        c = r.choice(tl.clips)
        with pytest.raises(AnchorRefused):
            anchor_at(tl, reg, c.track, c.start, "k")
        refused += 1
    assert refused == 300


def test_later_unsupported_gives_typed_state_never_a_position():
    for s in range(300):
        r, tl, reg = build(30_000_000 + s)
        c = r.choice(tl.clips)
        a = anchor_at(tl, reg, c.track, c.start, "k")
        tl.clips[tl.clips.index(c)].extra["speed"] = 1.5
        res = Resolver(tl, reg).resolve(a)
        assert res.state == "non_supportée" and not res.options, res
