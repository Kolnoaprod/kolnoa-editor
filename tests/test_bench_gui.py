"""Programme graphique d'essai : agrégation des tours, annulation, import sans Tkinter, dispatch du worker Qt."""

import queue
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "bench"))
import bench_common as bc  # noqa: E402
import bench_run as br  # noqa: E402


def _run(tk, r, med, err=()):
    return {"toolkit": tk, "variant": "proxy", "round": r, "errors": list(err),
            "metrics": {"startup_s": float(r), "scrub_ms": {"n": 5, "mediane": med, "p95": med * 2, "max": med * 3}}}


def test_aggregate_takes_median_of_rounds_and_worst_tail():
    res = {"runs": [_run("qt", 1, 10), _run("qt", 2, 30), _run("qt", 3, 20, ["boom"])]}
    a = bc.aggregate_rounds(res)
    (r,) = a["runs"]
    assert r["metrics"]["scrub_ms"] == {"n": 15, "mediane": 20, "p95": 60, "max": 90}
    assert r["metrics"]["startup_s"] == 2.0 and r["rounds_ok"] == 3 and r["errors"] == ["tour 3: boom"]
    assert len(a["rounds_detail"]) == 3
    assert "Tours individuels" in bc.render_report(a)


def test_single_round_is_left_untouched():
    res = {"runs": [_run("qt", 1, 10)]}
    assert bc.aggregate_rounds(res) is res


def test_gui_module_imports_without_tkinter_and_sets_rounds():
    import bench_gui
    assert bench_gui.ROUNDS == 3


def test_run_job_reports_cancel_and_error(tmp_path, monkeypatch):
    import bench_gui
    q, ev = queue.Queue(), threading.Event()
    monkeypatch.setattr(br, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(br.Cancelled()))
    bench_gui.run_job(["a", "b", "c"], tmp_path / "o", q, ev)
    assert q.get_nowait() == ("cancelled",)

    monkeypatch.setattr(br, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg absent")))
    bench_gui.run_job(["a", "b", "c"], tmp_path / "o", q, ev)
    kind, msg = q.get_nowait()
    assert kind == "error" and "ffmpeg absent" in msg and (tmp_path / "o" / "erreur.txt").exists()


def test_prepare_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        br.prepare([tmp_path / "nope.mp4"], tmp_path / "o")


def test_pipeline_stops_when_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "prepare", lambda *a, **k: {"media": [], "scenario": {}, "media_map": {"proxy": {}, "source": {}}})
    monkeypatch.setattr(br, "machine_info", lambda b: {"pyside6": None})
    monkeypatch.setattr(br, "find_browser", lambda c: None)
    with pytest.raises(br.Cancelled):
        br.run_pipeline(["x"], tmp_path, rounds=3, cancel=lambda: True)


def test_pipeline_runs_all_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(br, "prepare", lambda *a, **k: {"media": [], "scenario": {}, "media_map": {"proxy": {}, "source": {}}})
    monkeypatch.setattr(br, "machine_info", lambda b: {"pyside6": None})
    monkeypatch.setattr(br, "find_browser", lambda c: None)
    seen = []
    res = br.run_pipeline(["x"], tmp_path, rounds=3, progress=lambda d, t, l: seen.append((d, t)))
    assert len(res["runs"]) == 3 * 2 * 2 and seen[-1] == (12, 12)       # 3 tours × 2 variantes × (Qt, web)
    assert all(r["errors"] for r in res["runs"])                        # sans PySide6 ni navigateur : erreurs explicites, pas de crash
