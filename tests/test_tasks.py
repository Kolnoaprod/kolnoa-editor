"""Couche tâches (C1a) : états, progression, annulation (y compris le processus ffmpeg), erreurs, isolation des listeners."""

import threading
import time

import pytest

from kolnoa_editor.ffmpeg import Cancelled, ffmpeg_bin, run
from kolnoa_editor.tasks import MIN_PROGRESS_INTERVAL_S, Progress, TaskRunner, TaskState


@pytest.fixture()
def runner():
    r = TaskRunner()
    yield r
    r.cancel_all()
    r.shutdown()


def events_of(runner):
    evs = []
    runner.add_listener(evs.append)
    return evs


def test_successful_task_goes_through_the_states_and_returns_its_result(runner):
    evs = events_of(runner)
    t = runner.submit("calcul", lambda ctx, a, b=0: a + b, 2, b=3)
    assert t.wait(5) and t.state is TaskState.DONE and t.result == 5 and t.error is None
    states = [e.state for e in evs if e.kind == "state"]
    assert states == [TaskState.QUEUED, TaskState.RUNNING, TaskState.DONE]
    assert t.is_finished and t.elapsed_s >= 0


def test_failed_task_reports_the_error_keeps_the_traceback_and_the_runner_survives(runner):
    def boom(ctx):
        raise ValueError("pas de dossier")

    t = runner.submit("boom", boom)
    assert t.wait(5) and t.state is TaskState.FAILED
    assert t.error == "ValueError: pas de dossier" and isinstance(t.exception, ValueError)
    assert any("Traceback" in line for line in t.logs)
    ok = runner.submit("suite", lambda ctx: "ok")
    assert ok.wait(5) and ok.state is TaskState.DONE and ok.result == "ok"


def test_cancel_before_start_never_runs_the_function():
    r = TaskRunner(max_workers=1)
    gate, ran = threading.Event(), []
    a = r.submit("bloquante", lambda ctx: gate.wait(5))
    b = r.submit("attente", lambda ctx: ran.append(1))
    time.sleep(0.1)
    assert b.state is TaskState.QUEUED
    b.cancel()
    gate.set()
    assert a.wait(5) and b.wait(5)
    assert b.state is TaskState.CANCELLED and ran == []
    r.shutdown()


def test_cooperative_cancel_through_check_cancelled(runner):
    started = threading.Event()

    def work(ctx):
        started.set()
        for _ in range(500):
            ctx.check_cancelled()
            time.sleep(0.01)

    t = runner.submit("coop", work)
    assert started.wait(5)
    t.cancel()
    assert t.wait(5) and t.state is TaskState.CANCELLED and t.error is None


def test_a_function_that_ignores_the_token_is_still_reported_cancelled(runner):
    started = threading.Event()
    release = threading.Event()

    def stubborn(ctx):
        started.set()
        release.wait(5)
        return "fini quand même"

    t = runner.submit("têtue", stubborn)
    assert started.wait(5)
    t.cancel()
    release.set()
    assert t.wait(5) and t.state is TaskState.CANCELLED          # l'utilisateur a annulé : jamais présenté comme réussi


def test_cancel_stops_the_running_ffmpeg_process_quickly(runner):
    started = threading.Event()

    def long_encode(ctx):
        started.set()
        run([ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=60",
             "-t", "3000", "-f", "null", "-"], cancel=ctx.cancel)

    t = runner.submit("encode", long_encode)
    assert started.wait(5)
    time.sleep(0.5)
    t0 = time.monotonic()
    t.cancel()
    assert t.wait(15) and t.state is TaskState.CANCELLED
    assert time.monotonic() - t0 < 10                              # un encodage de 50 min est arrêté en quelques secondes


def test_progress_is_throttled_but_always_delivers_the_last_step_and_latest_value(runner):
    evs = events_of(runner)

    def work(ctx):
        for i in range(1, 3001):
            ctx.report("étape", i, 3000, f"fichier {i}")

    t = runner.submit("p", work)
    assert t.wait(10)
    prog = [e.progress for e in evs if e.kind == "progress"]
    assert 2 <= len(prog) < 1500                                   # pas une mise à jour par appel
    assert prog[-1].current == 3000 and prog[-1].fraction == 1.0 and prog[-1].eta_s is None
    cur = [p.current for p in prog]
    assert cur == sorted(cur)                                      # jamais de retour en arrière
    assert t.progress.current == 3000


def test_eta_and_fraction():
    assert Progress("x", 1, 4).fraction == 0.25
    assert Progress("x", 0, 0).fraction is None and Progress("x", 9, 4).fraction == 1.0
    r = TaskRunner()

    def work(ctx):
        ctx.report("a", 0, 10)
        time.sleep(MIN_PROGRESS_INTERVAL_S * 3)
        ctx.report("a", 5, 10)
        return ctx._task.progress

    t = r.submit("eta", work)
    t.wait(5)
    assert t.result.eta_s is not None and t.result.eta_s > 0
    r.shutdown()


def test_a_new_stage_restarts_the_clock_and_is_emitted_immediately(runner):
    evs = events_of(runner)

    def work(ctx):
        ctx.report("inventaire", 1, 2)
        ctx.report("audio", 0, 5)

    t = runner.submit("s", work)
    assert t.wait(5)
    stages = [e.progress.stage for e in evs if e.kind == "progress"]
    assert "inventaire" in stages and stages[-1] == "audio"


def test_a_broken_listener_never_breaks_the_task(runner):
    def bad(ev):
        raise RuntimeError("interface plantée")

    runner.add_listener(bad)
    evs = events_of(runner)
    t = runner.submit("ok", lambda ctx: 42)
    assert t.wait(5) and t.state is TaskState.DONE and t.result == 42
    assert any(e.state is TaskState.DONE for e in evs)


def test_one_task_at_a_time_by_default_and_parallel_when_asked():
    order = []

    def job(name, delay):
        def f(ctx):
            order.append(("début", name))
            time.sleep(delay)
            order.append(("fin", name))
        return f

    serial = TaskRunner(max_workers=1)
    a, b = serial.submit("a", job("a", 0.2)), serial.submit("b", job("b", 0.0))
    a.wait(5), b.wait(5)
    assert order == [("début", "a"), ("fin", "a"), ("début", "b"), ("fin", "b")]
    serial.shutdown()
    order.clear()
    par = TaskRunner(max_workers=2)
    a, b = par.submit("a", job("a", 0.3)), par.submit("b", job("b", 0.0))
    a.wait(5), b.wait(5)
    assert order.index(("début", "b")) < order.index(("fin", "a"))
    par.shutdown()


def test_logs_are_kept_and_bounded(runner):
    def work(ctx):
        for i in range(2500):
            ctx.log(f"ligne {i}")

    t = runner.submit("logs", work)
    assert t.wait(10)
    assert len(t.logs) == 2000 and t.logs[-1] == "ligne 2499"


def test_wait_with_timeout_reports_unfinished_tasks(runner):
    gate = threading.Event()
    t = runner.submit("lente", lambda ctx: gate.wait(5))
    assert t.wait(0.1) is False and not t.is_finished
    gate.set()
    assert t.wait(5)
