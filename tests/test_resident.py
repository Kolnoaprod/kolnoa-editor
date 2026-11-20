"""Moteur résident (sans Qt) et icône de notification : instance unique, réglages, démarrage Windows, tâches, fermeture en arrière-plan."""

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from kolnoa_editor.app import settings as S
from kolnoa_editor.app.autostart import Autostart, MemoryBackend
from kolnoa_editor.app.engine import EngineAlreadyRunning, ResidentEngine
from kolnoa_editor.app.instance import InstanceLock
from kolnoa_editor.app.jobs import JobRegistry
from kolnoa_editor.ffmpeg import Cancelled


# --- instance unique ---------------------------------------------------------------------------
def test_second_lock_refused_until_release(tmp_path):
    a, b = InstanceLock(tmp_path), InstanceLock(tmp_path)
    assert a.acquire() and a.held
    assert not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_lock_refused_across_processes_and_freed_on_exit(tmp_path):
    code = ("import sys,time; from pathlib import Path; from kolnoa_editor.app.instance import InstanceLock;"
            "l=InstanceLock(Path(sys.argv[1])); assert l.acquire(); print('ok',flush=True); time.sleep(30)")
    p = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdout=subprocess.PIPE, text=True,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    try:
        assert p.stdout.readline().strip() == "ok"
        assert not InstanceLock(tmp_path).acquire()
    finally:
        p.kill()
        p.wait()
    l = InstanceLock(tmp_path)
    assert l.acquire()                                       # plantage/fin du processus : le verrou est libéré par le système
    l.release()


def test_engine_refuses_second_engine(tmp_path):
    e1, e2 = ResidentEngine(tmp_path, Autostart(MemoryBackend())), ResidentEngine(tmp_path, Autostart(MemoryBackend()))
    e1.start()
    with pytest.raises(EngineAlreadyRunning):
        e2.start()
    e1.shutdown()
    e2.start()
    e2.shutdown()


# --- réglages / démarrage Windows ---------------------------------------------------------------
def test_settings_roundtrip_defaults_and_unknown_keys(tmp_path):
    p = tmp_path / "s.json"
    assert S.load(p) == S.AppSettings()                      # absent → défauts ; démarrage avec Windows jamais activé d'office
    assert S.AppSettings().start_with_windows is False
    p.write_text(json.dumps({**asdict(S.AppSettings()), "futur": 1, "start_with_windows": True}))
    assert S.load(p).start_with_windows is True
    p.write_text("{pas du json")
    assert S.load(p) == S.AppSettings()


def test_autostart_enable_disable_and_command(tmp_path):
    be = MemoryBackend()
    e = ResidentEngine(tmp_path, Autostart(be))
    e.start()
    assert not e.autostart.is_enabled()
    e.update_settings(start_with_windows=True)
    assert be.value and "--minimized" in be.value
    e.update_settings(start_minimized=False)
    assert "--minimized" not in be.value
    e.update_settings(start_with_windows=False)
    assert be.value is None
    assert S.load(tmp_path / "settings.json").start_with_windows is False
    with pytest.raises(KeyError):
        e.update_settings(inconnu=1)
    e.shutdown()


# --- tâches ---------------------------------------------------------------------------------------
def test_job_done_failed_cancelled_and_log(tmp_path):
    reg = JobRegistry(tmp_path / "jobs.jsonl")
    seen = []
    reg.subscribe(lambda j: seen.append((j.id, j.state)))
    ok = reg.run("export", "ok", lambda j: reg.update(j, progress=0.5) or 42)
    bad = reg.run("export", "bad", lambda j: 1 / 0)

    def slow(j):
        while True:
            time.sleep(0.01)
            j.cancel.raise_if_cancelled()
    c = reg.run("analyse", "slow", slow)
    time.sleep(0.1)
    assert reg.cancel(c.id)
    assert reg.wait_idle(5)
    assert (ok.state, bad.state, c.state) == ("done", "failed", "cancelled")
    assert "division" in bad.message
    states = {s for i, s in seen if i == ok.id}
    assert {"queued", "running", "done"} <= states
    lines = [json.loads(l) for l in (tmp_path / "jobs.jsonl").read_text().splitlines()]
    assert sorted(l["state"] for l in lines) == ["cancelled", "done", "failed"] and all("cancel" not in l for l in lines)
    assert not reg.cancel(ok.id)                             # déjà terminée


def test_observer_failure_never_kills_a_job():
    reg = JobRegistry()
    reg.subscribe(lambda j: 1 / 0)
    j = reg.run("x", "x", lambda j: 1)
    assert reg.wait_idle(5) and j.state == "done"


def test_shutdown_cancels_active_jobs(tmp_path):
    e = ResidentEngine(tmp_path, Autostart(MemoryBackend()))
    e.start()

    def slow(j):
        while True:
            time.sleep(0.01)
            j.cancel.raise_if_cancelled()
    j = e.jobs.run("export", "long", slow)
    time.sleep(0.05)
    assert e.status()["jobs"][0]["state"] == "running"
    e.shutdown()
    assert j.state == "cancelled" and not e.lock.held


@pytest.mark.skipif(sys.platform != "win32", reason="registre Windows")
def test_real_windows_registry_autostart_roundtrip():
    from kolnoa_editor.app.autostart import VALUE, WinRegistryBackend
    be = WinRegistryBackend()
    before = be.get()
    try:
        a = Autostart(be)
        a.enable(True)
        assert a.is_enabled() and be.get().endswith("--minimized")
        a.disable()
        assert not a.is_enabled()
    finally:
        be.set(before) if before else be.delete()
