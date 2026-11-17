"""Lecteur multicam : horloge sans dérive, planificateur de décodage, changement d'angle sans gel, prise = une annulation."""

import pytest

from kolnoa_editor.player.backend import FakeBackend
from kolnoa_editor.player.clock import MasterClock
from kolnoa_editor.player.controller import MulticamPlayer
from kolnoa_editor.player.scheduler import ACTIVE, COLD, NEXT, PAUSED, WARM, MediaInfo, plan
from kolnoa_editor.timeline import Session, TimelineError
from tests.test_multicam_live import tl_cam


class FakeTime:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def info(name, w=3840, h=2160, fps=50, proxy=True, duration=600.0):
    return MediaInfo(f"/src/{name}.mp4", w, h, fps, f"/prx/{name}.mp4" if proxy else None, 540, duration)


MEDIA = {"A7": info("a7"), "OSMO": info("osmo", fps=25), "DRONE": info("drone", fps=30)}
to_angle = lambda a, t: t - {"A7": 12.0, "OSMO": 3.0, "DRONE": 0.0}[a]


def make(speed_err=None, seek_s=0.15, lag=False, **kw):
    ft = FakeTime()
    speed_err = speed_err or {}
    mk = lambda a: FakeBackend(ft, seek_s=seek_s, speed_err=speed_err.get(a, 0.0), lag=lag)
    tl = tl_cam()
    p = MulticamPlayer(tl, "g001", "V1", MEDIA, mk, MasterClock(ft), now=ft, **kw)
    return p, ft, tl


def run(p, ft, seconds, step=1 / 30):
    for _ in range(int(seconds / step)):
        ft.t += step
        p.tick()


# --- horloge ------------------------------------------------------------------------------------------------
def test_clock_no_drift_over_hours_and_pause_resume():
    ft = FakeTime()
    c = MasterClock(ft)
    c.seek(10.0)
    c.play()
    for _ in range(3 * 3600 * 30):               # 3 h de ticks irréguliers
        ft.t += 1 / 30
    assert c.position() == pytest.approx(10.0 + 3 * 3600, abs=1e-6 * 3 * 3600)
    c.pause(); p = c.position(); ft.t += 50
    assert c.position() == p
    c.play(2.0); ft.t += 5
    assert c.position() == pytest.approx(p + 10)
    with pytest.raises(ValueError):
        c.play(0)


# --- planificateur ------------------------------------------------------------------------------------------
def roles(slots):
    return {s.angle: s.role for s in slots}


def test_active_gets_source_when_hardware_decodes_and_budget_allows():
    s = plan(MEDIA, "A7", to_angle, 20.0, budget=5e8, hw_decode=True)
    a7 = next(x for x in s if x.angle == "A7")
    assert (a7.role, a7.quality, a7.priority) == (ACTIVE, "source", 0)
    assert roles(s) == {"A7": ACTIVE, "OSMO": WARM, "DRONE": WARM}
    assert all(x.quality == "proxy" for x in s if x.angle != "A7")


def test_active_falls_back_to_proxy_without_hardware_or_budget():
    for kw in ({"budget": 5e8, "hw_decode": False}, {"budget": 1e8, "hw_decode": True}):
        s = plan(MEDIA, "A7", to_angle, 20.0, **kw)
        assert next(x for x in s if x.angle == "A7").quality == "proxy"


def test_over_budget_pauses_extra_angles_never_the_active_one():
    many = {f"C{i}": info(f"c{i}") for i in range(9)}
    s = plan(many, "C0", lambda a, t: t, 5.0, budget=3 * many["C0"].proxy_cost)
    r = roles(s)
    assert r["C0"] == ACTIVE
    assert sum(1 for v in r.values() if v in (WARM, NEXT)) <= 2
    assert sum(1 for v in r.values() if v == PAUSED) >= 6


def test_upcoming_cut_is_prioritised_even_when_over_budget():
    many = {f"C{i}": info(f"c{i}") for i in range(9)}
    s = plan(many, "C0", lambda a, t: t, 5.0, budget=2 * many["C0"].proxy_cost, upcoming=[(6.0, "C7")])
    c7 = next(x for x in s if x.angle == "C7")
    assert c7.role == NEXT and c7.priority == 1 and c7.quality == "proxy"


def test_angle_not_started_or_finished_is_cold():
    media = {"A7": info("a7", duration=10.0), "OSMO": info("osmo")}
    off = {"A7": 12.0, "OSMO": 3.0}
    f = lambda a, t: t - off[a]
    assert roles(plan(media, "OSMO", f, 5.0, budget=1e9))["A7"] == COLD      # t_angle < 0
    assert roles(plan(media, "OSMO", f, 40.0, budget=1e9))["A7"] == COLD     # > durée


def test_planner_is_deterministic():
    assert plan(MEDIA, "A7", to_angle, 20.0, budget=5e8) == plan(MEDIA, "A7", to_angle, 20.0, budget=5e8)


# --- contrôleur ---------------------------------------------------------------------------------------------
def test_all_angles_follow_the_common_clock_without_drift_even_with_fast_and_slow_decoders():
    p, ft, _ = make(speed_err={"A7": 0.002, "OSMO": -0.004, "DRONE": 0.001})
    p.seek(30.0)
    run(p, ft, 0.5)
    p.play()
    run(p, ft, 120)                                      # 2 min : sans correction, OSMO s'écarterait de ~0,5 s
    t = p.clock.position()
    for a, be in p.backends.items():
        if a in p._open:
            assert abs(be.position() - to_angle(a, t)) < 0.06, a
    assert p.stats.nudges > 0 and p.stats.reseeks == 0   # rattrapé en douceur, sans saccade


def test_large_gap_triggers_reseek():
    p, ft, _ = make()
    p.seek(30.0); run(p, ft, 0.5); p.play(); run(p, ft, 1)
    p.backends["OSMO"].seek(1.0)                         # le décodeur « décroche »
    run(p, ft, 1)
    assert p.stats.reseeks >= 1
    assert abs(p.backends["OSMO"].position() - to_angle("OSMO", p.clock.position())) < 0.1


def test_switch_does_not_freeze_display_when_target_is_warm():
    p, ft, _ = make()
    p.seek(30.0); run(p, ft, 0.5); p.play(); run(p, ft, 2)
    assert p.shown == "A7"
    p.press(2)
    ft.t += 1 / 30; p.tick()
    assert p.shown == "OSMO"                             # angle chaud : bascule sur la prochaine image
    assert p.stats.switch_latencies[-1] <= 0.05 and p.stats.forced_swaps == 0


def test_switch_to_cold_angle_keeps_previous_image_until_ready():
    p, ft, _ = make(seek_s=0.4, budget=1.0)              # budget minuscule : seul l'actif décode, les autres sont en pause
    p.seek(30.0); run(p, ft, 1.0); p.play(); run(p, ft, 2)
    p.press(3)
    shown = []
    for _ in range(40):
        ft.t += 1 / 30; p.tick(); shown.append(p.shown)
    assert shown[0] == "A7"                              # l'ancienne image reste affichée pendant l'attente (pas de noir, pas de gel)
    assert shown[-1] == "DRONE"
    assert p.stats.forced_swaps == 0 and 0.0 < p.stats.switch_latencies[-1] < 0.75


def test_forced_swap_after_timeout_is_counted():
    p, ft, _ = make(seek_s=5.0, budget=1.0)
    p.seek(30.0); run(p, ft, 6); p.play(); run(p, ft, 1)
    p.press(3)
    run(p, ft, 1.0)
    assert p.shown == "DRONE" and p.stats.forced_swaps == 1


def test_active_angle_has_top_priority_and_is_disciplined_first():
    p, ft, _ = make()
    p.seek(30.0); run(p, ft, 0.5); p.play(); run(p, ft, 1)
    assert p.backends["A7"].priority == 0
    p.press(2); run(p, ft, 0.2)
    assert p.backends["OSMO"].priority == 0 and p.backends["A7"].priority > 0


# --- prise = opérations réversibles du moteur ---------------------------------------------------------------
def test_take_commits_as_one_undo_step_and_nothing_before(tmp_path):
    t0 = tl_cam()
    t0.group("g001").angle("OSMO").status = "ok"
    sess = Session.create(tmp_path / "s", t0)
    tl = sess.tl
    ft = FakeTime()
    p = MulticamPlayer(tl, "g001", "V1", MEDIA, lambda a: FakeBackend(ft), MasterClock(ft), now=ft)
    p.seek(5.0)                                          # image 125 : dans le plan multicam [100, 300)
    run(p, ft, 0.5)
    before = [(c.start, c.end, c.angle) for c in tl.clips_on("V1")]
    p.play(record=True)
    run(p, ft, 1.0); p.press(2)
    run(p, ft, 1.0); p.press(1)
    assert [(c.start, c.end, c.angle) for c in tl.clips_on("V1")] == before     # rien écrit pendant la prise
    tx = p.commit()
    tl.validate()
    s2 = sess
    s2.do(tx, "prise multicam")
    after = [c.angle for c in s2.tl.clips_on("V1")]
    assert after[0:1] == ["A7"] and "OSMO" in after and len(after) >= 3
    s2.undo()
    assert [(c.start, c.end, c.angle) for c in s2.tl.clips_on("V1")] == before


def test_take_with_unsynced_angle_is_refused_whole_and_keys_are_validated():
    p, ft, tl = make()
    p.seek(5.0); run(p, ft, 0.5); p.play(record=True)
    run(p, ft, 0.5); p.press(2); run(p, ft, 0.5); p.press(3)      # DRONE non synchronisé
    with pytest.raises(TimelineError):
        p.commit()
    with pytest.raises(TimelineError):
        p.press(4)
    p.discard()
    assert p.take == []


def test_no_recording_when_paused_press_only_changes_view():
    p, ft, _ = make()
    p.seek(5.0); run(p, ft, 0.5)
    p.press(2); run(p, ft, 0.5)
    assert p.shown == "OSMO" and p.take == []


# --- PR #19 revue : rafraîchissement en pause, seeks successifs, cadence réelle vs média, budget ----------------
def positions_ok(p, tol=0.06):
    t = p.clock.position()
    return {a: abs(be.position() - p.t_angle(a, t)) for a, be in p.backends.items() if a in p._open} and \
        max(abs(be.position() - p.t_angle(a, t)) for a, be in p.backends.items() if a in p._open) < tol


def test_successive_seeks_while_paused_converge_on_the_last_one_without_seek_storm():
    p, ft, _ = make(seek_s=0.3, lag=True)                      # seeks asynchrones : la position n'arrive que 0,3 s plus tard
    p.seek(20.0); run(p, ft, 1.0)
    for t in (30.0, 45.0, 60.0):                               # trois seeks à 30 ms d'intervalle
        p.seek(t); run(p, ft, 0.03)
    n0 = p.stats.seeks_issued
    run(p, ft, 1.5)
    assert p.clock.position() == pytest.approx(60.0)
    assert positions_ok(p)                                     # tous les angles ouverts sont sur la DERNIÈRE cible, aucun sur une cible périmée
    assert p.stats.seeks_issued - n0 <= 2 * len(p._open)       # pas de rafale : au plus une reprise par angle après les 3 seeks
    for a, be in p.backends.items():
        if a in p._open:
            assert be._landing is None


def test_paused_angle_that_lands_wrong_is_refreshed_once_not_every_tick():
    p, ft, _ = make(seek_s=0.3, lag=True)
    p.seek(30.0); run(p, ft, 1.0)
    be = p.backends["OSMO"]
    be.seek(5.0)                                               # le décodeur de l'angle est ailleurs (seek raté / ancien)
    run(p, ft, 0.25)                                           # le seek parasite n'a pas encore atterri
    n_before = sum(1 for e in be.log if e[0] == "seek")
    run(p, ft, 2.0)
    n_after = sum(1 for e in be.log if e[0] == "seek")
    assert abs(be.position() - to_angle("OSMO", 30.0)) < 0.06  # recalé
    assert n_after - n_before <= 2                             # 1 correction (+1 de marge), pas une par image (30 Hz)


def test_refresh_cadence_is_wall_clock_not_media_time():
    for rate in (1.0, 4.0):
        p, ft, _ = make(budget=1.0)                            # budget minuscule : OSMO et DRONE sont « en pause »
        p.seek(30.0); run(p, ft, 0.5)
        p.clock.play(rate); p._dirty = True
        run(p, ft, 0.2)
        assert p.backends["OSMO"].path is not None and any(s.role == "paused" for s in p.slots)
        n0 = sum(1 for e in p.backends["OSMO"].log if e[0] == "seek")
        run(p, ft, 4.0)                                        # 4 s RÉELLES
        n = sum(1 for e in p.backends["OSMO"].log if e[0] == "seek") - n0
        assert 5 <= n <= 9, (rate, n)                          # ~ une reprise toutes les 0,5 s réelles, quelle que soit la vitesse (pas ×rate)


def mi(name, proxy=True, w=3840, h=2160, fps=50):
    return info(name, w, h, fps, proxy)


def test_planner_reports_missing_proxy_and_budget_overrun_never_silently():
    media = {"A7": mi("a7", proxy=False), "OSMO": mi("osmo", proxy=False, fps=25), "DRONE": mi("drone")}
    budget = 2.0 * media["DRONE"].proxy_cost                   # ~ 2 proxys 540p, mais PAS une source 4K50
    pl = plan(media, "A7", to_angle, 20.0, budget=budget, hw_decode=True)
    assert isinstance(pl, list) and len(pl) == 3               # reste utilisable comme une liste de Slot
    assert pl.over_budget and pl.spent > pl.budget             # l'actif sans proxy dépasse : c'est dit
    assert any("A7" in w and "pas de proxy" in w for w in pl.warnings)
    assert "A7" in pl.missing_proxy and "OSMO" in pl.missing_proxy
    osmo = next(x for x in pl if x.angle == "OSMO")
    assert osmo.role == PAUSED and any("OSMO" in w for w in pl.warnings)      # non actif sans proxy ni budget : en pause, pas lu en 4K
    ok = plan(MEDIA, "A7", to_angle, 20.0, budget=1e10)
    assert not ok.over_budget and ok.warnings == () and ok.missing_proxy == ()


def test_controller_exposes_planner_warnings_and_counts_overruns():
    media = {"A7": mi("a7", proxy=False), "OSMO": mi("osmo"), "DRONE": mi("drone")}
    ft = FakeTime()
    p = MulticamPlayer(tl_cam(), "g001", "V1", media, lambda a: FakeBackend(ft), MasterClock(ft), now=ft, budget=1e7)
    p.seek(30.0)
    assert p.warnings and p.stats.over_budget_replans >= 1 and p.report.over_budget
