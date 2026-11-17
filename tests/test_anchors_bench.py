"""A1-3 : le banc tourne (tailles réduites) et ses résultats sont cohérents ; le seuil de 5 ms n'est PAS un test (dépend de la machine)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("bench_anchors", Path(__file__).resolve().parents[1] / "tools/bench/bench_anchors.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_quick_bench_runs_and_is_consistent():
    row = bench.run(300, 30, repeats=2)
    assert row["clips"] == 300 and row["anchors"] == 30
    assert 0 < row["warm_p50_ms"] <= row["warm_p95_ms"] <= row["warm_p99_ms"]
    assert row["warm_p99_ms"] < 100            # garde-fou grossier contre une régression en O(n) par ancre
    assert "| 300 | 30 |" in bench.to_markdown([row], bench.machine())


def test_bench_timeline_is_synthetic_only():
    tl, reg, _ = bench.build(50)
    assert all(c.media.startswith("rush") for c in tl.clips)
