"""Outils d'essai Qt / web : scénario déterministe, statistiques, rapport honnête, serveur avec Range."""

import json
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "bench"))
import bench_common as bc  # noqa: E402


ANGLES = [{"id": "C1", "key": 1, "duration_s": 60.0}, {"id": "C2", "key": 2, "duration_s": 45.0}]


def test_summarize_ignores_missing_and_empty():
    assert bc.summarize([]) is None and bc.summarize(None) is None and bc.summarize([float("nan")]) is None
    s = bc.summarize([5, 1, 3, None, 100])
    assert s == {"n": 4, "mediane": 5, "p95": 100, "max": 100}


def test_scenario_is_deterministic_and_bounded():
    a, b = bc.build_scenario(ANGLES), bc.build_scenario(ANGLES)
    assert a == b                                              # mêmes gestes pour Qt et web
    assert bc.build_scenario(ANGLES, seed=1) != a
    assert len(a["clips"]) == 800 and len(a["tracks"]) == 10  # 2 vidéo + 8 audio
    assert a["play_s"] <= 44.0                                 # borné par la plus courte vidéo moins 1 s
    assert all(0 <= e["t"] < a["play_s"] for e in a["angle_events"]) and {e["key"] for e in a["angle_events"]} <= {1, 2}
    assert all(0 < s <= 44.0 for s in a["scrubs"]) and len(a["drags"]) == 30
    assert all(0 <= c["track"] < 10 and c["dur"] > 0 for c in a["clips"])
    with pytest.raises(ValueError):
        bc.build_scenario([])


def test_report_never_invents_numbers():
    res = {"machine": {"os": "Windows 11"}, "media": [], "scenario": {},
           "runs": [{"toolkit": "qt", "variant": "proxy", "errors": [],
                     "metrics": {"scrub_ms": bc.summarize([10, 12, 30])}, "resources": {}}]}
    md = bc.render_report(res)
    assert "méd. 12 · p95 30" in md                              # la valeur mesurée apparaît
    assert md.count(bc.NM) >= 10                                 # tout le reste est « non mesuré »
    assert "Windows 11" in md and "Web local" in md
    assert "Erreurs" not in md


def test_resource_summary_window():
    samples = [{"t": t, "cpu": c, "rss_mo": 100 + t, "gpu": None} for t, c in ((1, 10), (2, 20), (3, 90), (9, 5))]
    r = bc.resource_summary(samples, 1.5, 3.5)
    assert r["cpu"]["mediane"] == 90 and r["ram_mo_max"] == 103 and r["gpu_pct"] is None
    assert bc.resource_summary(samples, 20, 30) is None


def test_proxy_never_overwrites_source(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    with pytest.raises(ValueError):
        bc.make_proxy(f, f)


def test_local_server_supports_range_and_collects_results(tmp_path):
    import bench_run
    f = tmp_path / "m.bin"
    f.write_bytes(bytes(range(256)) * 4)
    srv = bench_run.Server({"/media/x": f})
    try:
        base = f"http://127.0.0.1:{srv.port}"
        req = urllib.request.Request(base + "/media/x", headers={"Range": "bytes=10-19"})
        with urllib.request.urlopen(req) as r:
            assert r.status == 206 and r.read() == bytes(range(10, 20))
            assert r.headers["Content-Range"] == "bytes 10-19/1024"
        with urllib.request.urlopen(base + "/media/x") as r:
            assert r.status == 200 and len(r.read()) == 1024
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/etc/passwd")          # seuls les fichiers listés sont servis
        urllib.request.urlopen(urllib.request.Request(base + "/web_proxy", data=json.dumps({"a": 1}).encode(), method="POST"))
        assert srv.results["web_proxy"] == {"a": 1}
    finally:
        srv.close()


def test_report_resource_rows_share_phases():
    res = {"runs": [
        {"toolkit": "qt", "variant": "proxy", "metrics": {}, "errors": [],
         "resources": {"scrub": {"cpu": bc.summarize([10, 20]), "ram_mo_max": 150.0, "gpu_pct": None}}},
        {"toolkit": "web", "variant": "proxy", "metrics": {}, "errors": [],
         "resources": {"scrub": {"cpu": bc.summarize([30]), "ram_mo_max": 900.0, "gpu_pct": None}, "drag_io": None}}]}
    md = bc.render_report(res)
    row = [line for line in md.splitlines() if line.startswith("| Ressources · scrub")][0]
    assert "RAM max 150.0 Mo" in row and "RAM max 900.0 Mo" in row            # une ligne, deux colonnes
    drag = [line for line in md.splitlines() if line.startswith("| Ressources · drag_io")][0]
    assert drag.count(bc.NM) == 2
