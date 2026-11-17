"""Banc de performance des ancres (A1-3) : résolution à froid / à chaud, p50/p95/p99, sur des tailles réalistes.

    python tools/bench/bench_anchors.py            # tailles complètes, écrit docs/ANCRES_PERF.md
    python tools/bench/bench_anchors.py --quick    # tailles réduites (test de fumée)

Synthétique : aucun nom de client, aucun vrai fichier. Froid = nouvel index (construction du `Resolver`) + première résolution ;
chaud = résolution d'une ancre sur un index déjà construit (médiane de plusieurs passes). Seuil visé 5 ms par ancre ; il n'est PAS
promis avant mesure : les chiffres ci-dessous sont ceux de la machine indiquée."""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from kolnoa_editor.anchors import MediaRegistry, Resolver, anchor_at, range_between  # noqa: E402
from kolnoa_editor.timeline.model import Clip, Timeline, Track  # noqa: E402

SIZES = [(200, 20), (2000, 200), (10000, 1000), (50000, 5000)]
QUICK = [(200, 20), (1000, 100)]


def build(n_clips: int, seed: int = 7):
    r = random.Random(seed)
    tracks = 8
    tl = Timeline(title="bench", fps=25, tracks=[Track(f"V{i}") for i in range(tracks)])
    reg = MediaRegistry(id_factory=lambda: f"m{len(reg.entries):04d}")
    medias = [f"rush{i:03d}.mov" for i in range(max(10, n_clips // 200))]
    for m in medias:
        reg.register(m, 1000, 1, duration_us=7_200_000_000, fps="25/1", quick_hash=m)
    pos = [0] * tracks
    for i in range(n_clips):
        t = i % tracks
        dur = r.randint(25, 250)
        tl.clips.append(Clip(f"c{i}", f"V{t}", r.choice(medias), pos[t], dur, src_in=round(r.randint(0, 170000) / 25, 6)))
        pos[t] += dur + r.choice((0, 0, 0, r.randint(1, 50)))
    return tl, reg, r


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def run(n_clips: int, n_anchors: int, repeats: int = 5) -> dict:
    tl, reg, r = build(n_clips)
    anchors = []
    while len(anchors) < n_anchors:
        c = r.choice(tl.clips)
        if r.random() < 0.7:
            anchors.append(anchor_at(tl, reg, c.track, r.randrange(c.start, c.end), f"a{len(anchors)}"))
        elif c.duration > 4:
            a = r.randrange(c.start, c.end - 1)
            anchors.append(range_between(tl, reg, c.track, a, r.randrange(a + 1, c.end + 1), f"a{len(anchors)}"))
    cold_build, cold_first = [], []
    for _ in range(repeats):
        t0 = time.perf_counter()
        res = Resolver(tl, reg)
        t1 = time.perf_counter()
        res.resolve(anchors[0])
        t2 = time.perf_counter()
        cold_build.append((t1 - t0) * 1e3)
        cold_first.append((t2 - t1) * 1e3)
    warm: list[float] = []
    res = Resolver(tl, reg)
    for _ in range(repeats):
        for a in anchors:
            t0 = time.perf_counter()
            res.resolve(a)
            warm.append((time.perf_counter() - t0) * 1e3)
    # lot : toutes les ancres d'un coup, index froid compris
    t0 = time.perf_counter()
    batch = Resolver(tl, reg)
    for a in anchors:
        batch.resolve(a)
    batch_ms = (time.perf_counter() - t0) * 1e3
    return {"clips": n_clips, "anchors": n_anchors,
            "cold_index_build_ms": round(statistics.median(cold_build), 2), "cold_first_resolve_ms": round(statistics.median(cold_first), 3),
            "warm_p50_ms": round(pct(warm, 50), 4), "warm_p95_ms": round(pct(warm, 95), 4), "warm_p99_ms": round(pct(warm, 99), 4),
            "batch_total_ms": round(batch_ms, 1)}


def machine() -> dict:
    return {"python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor() or platform.machine(),
            "cpus": __import__("os").cpu_count()}


def to_markdown(rows: list[dict], info: dict) -> str:
    head = "| plans | ancres | index froid (ms) | 1re résolution froide (ms) | chaud p50 (ms) | chaud p95 (ms) | chaud p99 (ms) | lot complet (ms) |\n|---|---|---|---|---|---|---|---|\n"
    body = "".join(f"| {r['clips']} | {r['anchors']} | {r['cold_index_build_ms']} | {r['cold_first_resolve_ms']} | {r['warm_p50_ms']} | "
                  f"{r['warm_p95_ms']} | {r['warm_p99_ms']} | {r['batch_total_ms']} |\n" for r in rows)
    return head + body + f"\nMachine : {info['platform']}, {info['cpus']} cœurs, Python {info['python']}.\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", help="fichier Markdown à écrire")
    args = ap.parse_args(argv)
    rows = [run(n, a, 3 if args.quick else 5) for n, a in (QUICK if args.quick else SIZES)]
    md = to_markdown(rows, machine())
    print(md)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
