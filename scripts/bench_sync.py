#!/usr/bin/env python
"""Bench reproductible de la synchronisation (signaux synthétiques, vérité terrain connue).

    python scripts/bench_sync.py              # écrit docs/SYNC_METRICS.md
    python scripts/bench_sync.py --quick      # moins de tirages (contrôle rapide)

Les erreurs et taux ne dépendent que des graines fixes ; seuls les temps de calcul dépendent de la machine.
"""

from __future__ import annotations

import argparse
import platform
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kolnoa_editor.sync.audio import estimate_audio_sync          # noqa: E402
from kolnoa_editor.sync.config import SyncConfig                  # noqa: E402
from kolnoa_editor.sync.sources import ArraySource                # noqa: E402
from kolnoa_editor.sync.synth import extract, luma_scene, speech_like   # noqa: E402
from kolnoa_editor.sync.visual import LumaSeries, propose_visual_sync   # noqa: E402

FS = 8000
TOL = 0.040          # une image à 25 i/s


def pct(values, q):
    return float(np.percentile(values, q)) if len(values) else float("nan")


def run_audio(master_src, master, offset, dur, **kw):
    a = extract(master, FS, offset, dur, **kw)
    return estimate_audio_sync(master_src, ArraySource(a, FS))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "docs" / "SYNC_METRICS.md"))
    args = ap.parse_args()
    n = 4 if args.quick else 12
    master = speech_like(600, seed=5, rate=FS)
    ref = ArraySource(master, FS)
    rows: list[str] = []
    t_start = time.perf_counter()

    def add(title, header, body):
        rows.append(f"\n### {title}\n\n| {' | '.join(header)} |\n| {' | '.join('---' for _ in header)} |")
        rows.extend(f"| {' | '.join(str(c) for c in r)} |" for r in body)

    # 1. décalages, propres
    rng = np.random.default_rng(0)
    errs, times, st, fake = [], [], Counter(), 0
    for k in range(n * 2):
        off = float(rng.uniform(-25, 400))
        est = run_audio(ref, master, off, float(rng.uniform(40, 150)), snr_db=25, seed=k)
        st[est.status] += 1
        times.append(est.elapsed_s)
        if est.status in ("ok", "incertain"):
            errs.append(abs(est.offset - off) * 1000)
            fake += est.status == "ok" and abs(est.offset - off) > TOL
    add("Décalages positifs et négatifs (SNR 25 dB)", ["tirages", "états", "erreur médiane (ms)", "p95 (ms)", "max (ms)",
                                                       "faux « ok »", "temps médian (s)"],
        [[n * 2, dict(st), f"{statistics.median(errs):.2f}", f"{pct(errs, 95):.2f}", f"{max(errs):.2f}", fake,
          f"{statistics.median(times):.2f}"]])

    # 2. bruit
    body = []
    for snr in (20, 10, 0, -5, -10, -15):
        errs, st, fake = [], Counter(), 0
        for k in range(n):
            off = float(rng.uniform(0, 400))
            est = run_audio(ref, master, off, 90.0, snr_db=snr, color=True, gain=0.4, seed=50 + k)
            st[est.status] += 1
            if est.status in ("ok", "incertain"):
                errs.append(abs(est.offset - off) * 1000)
                fake += est.status == "ok" and abs(est.offset - off) > TOL
        body.append([f"{snr} dB", n, dict(st), f"{pct(errs, 50):.2f}" if errs else "-",
                     f"{pct(errs, 95):.2f}" if errs else "-", fake])
    add("Bruit et micro différent (passe-bas + écho, gain 0,4)", ["SNR", "tirages", "états", "erreur médiane (ms)",
                                                                "p95 (ms)", "faux « ok »"], body)

    # 3. silences
    body = []
    for name, sil in (("1 silence de 45 s", [(10, 55)]), ("2 silences (45 s + 20 s)", [(10, 55), (100, 120)]),
                      ("silence 70 % du clip", [(5, 75)])):
        errs, st = [], Counter()
        for k in range(n):
            off = float(rng.uniform(0, 400))
            est = run_audio(ref, master, off, 120.0, silence=sil, seed=70 + k)
            st[est.status] += 1
            if est.status in ("ok", "incertain"):
                errs.append(abs(est.offset - off) * 1000)
        body.append([name, n, dict(st), f"{pct(errs, 50):.2f}" if errs else "-", f"{pct(errs, 95):.2f}" if errs else "-"])
    add("Silences dans l'angle", ["cas", "tirages", "états", "erreur médiane (ms)", "p95 (ms)"], body)

    # 4. dérive
    body = []
    for drift in (-3000, -1000, -300, -50, 50, 300, 1000, 3000):
        derr, oerr, st = [], [], Counter()
        for k in range(max(3, n // 2)):
            off = float(rng.uniform(0, 300))
            est = run_audio(ref, master, off, 240.0, drift_ppm=drift, snr_db=20, seed=90 + k)
            st[est.status] += 1
            if est.status in ("ok", "incertain"):
                derr.append(abs(est.drift_ppm - drift))
                oerr.append(abs(est.offset - off) * 1000)
        body.append([f"{drift:+d} ppm", dict(st), f"{pct(derr, 50):.2f}" if derr else "-",
                     f"{max(derr):.2f}" if derr else "-", f"{max(oerr):.2f}" if oerr else "-"])
    add("Dérive d'horloge (clip de 4 min, SNR 20 dB)", ["dérive vraie", "états", "erreur médiane (ppm)",
                                                        "erreur max (ppm)", "erreur max du décalage (ms)"], body)

    # 5. sans correspondance
    st, times = Counter(), []
    for k in range(25 if args.quick else 100):
        other = speech_like(90, seed=1000 + k, rate=FS)
        est = estimate_audio_sync(ref, ArraySource(other, FS))
        st[est.status] += 1
        times.append(est.elapsed_s)
    add("Audio sans aucun rapport avec le maître", ["tirages", "états", "faux « ok »", "temps médian (s)"],
        [[sum(st.values()), dict(st), st["ok"], f"{statistics.median(times):.2f}"]])

    # 6. discontinuité
    ok = 0
    trials = max(3, n // 2)
    for k in range(trials):
        a = extract(master, FS, 20.0 + k, 80.0, seed=300 + k)
        b = extract(master, FS, 230.0 + k, 80.0, seed=400 + k)
        est = estimate_audio_sync(ref, ArraySource(np.concatenate([a, b]), FS), SyncConfig(wide_search_s=250.0))
        ok += est.status == "discontinu" and len(est.segments) == 2
    add("Clip avec un saut interne (audio recollé)", ["tirages", "détectés « discontinu » à 2 segments"], [[trials, ok]])

    # 7. temps de calcul
    body = []
    long_master = speech_like(1800, seed=21, rate=FS)
    long_ref = ArraySource(long_master, FS)
    for dur in (60, 300, 600):
        a = extract(long_master, FS, 400.0, float(dur), drift_ppm=100, snr_db=15, seed=5)
        t = time.perf_counter()
        est = estimate_audio_sync(long_ref, ArraySource(a, FS))
        body.append([f"{dur} s", "30 min", est.status, f"{abs(est.offset - 400.0) * 1000:.2f}", f"{time.perf_counter() - t:.2f}"])
    add("Temps de calcul (tableaux en mémoire ; la lecture de vrais fichiers par ffmpeg s'ajoute)",
        ["clip", "maître", "état", "erreur (ms)", "temps (s)"], body)

    # 8. visuel
    cfg = SyncConfig()
    fps = cfg.visual_fps
    shared = [20.0, 55.0, 90.0, 140.0]
    st_sh, errs = Counter(), []
    for k in range(40 if args.quick else 100):
        off = 15 + k * 0.37
        r_ = LumaSeries(luma_scene(200, fps, 100 + k, events=shared, private_events=3), fps)
        s_ = LumaSeries(luma_scene(120, fps, 200 + k, events=[e - off for e in shared if 3 < e - off < 117],
                                   private_events=4), fps)
        res = propose_visual_sync(r_, s_, cfg)
        good = bool(res.candidates) and abs(res.candidates[0].offset - off) <= 0.25
        st_sh[(res.status, "juste" if good else "faux")] += 1
        if res.status in ("proposition", "faible", "ambigu") and good:
            errs.append(abs(res.candidates[0].offset - off))
    st_un = Counter()
    for k in range(100 if args.quick else 500):
        r_ = LumaSeries(luma_scene(200, fps, 300 + k, events=[30, 80, 150], private_events=2), fps)
        s_ = LumaSeries(luma_scene(120, fps, 400 + k, events=[10, 70], private_events=2), fps)
        st_un[propose_visual_sync(r_, s_, cfg).status] += 1
    add("Synchro visuelle (drone muet) — EXPÉRIMENTAL, 4 événements lumineux communs",
        ["scénario", "tirages", "répartition (état, vérité)", "erreur médiane du 1er candidat (s)"],
        [["événements communs", sum(st_sh.values()), {f"{k[0]}/{k[1]}": v for k, v in sorted(st_sh.items())},
          f"{statistics.median(errs):.3f}" if errs else "-"],
         ["scènes sans rapport", sum(st_un.values()), dict(st_un), "-"]])

    header = f"""# Métriques de synchronisation (signaux synthétiques)

Généré par `python scripts/bench_sync.py` — Python {platform.python_version()}, numpy {np.__version__}, durée du bench
{time.perf_counter() - t_start:.0f} s. Graines fixes : erreurs et taux reproductibles ; les temps dépendent de la machine.

**Ce que ces chiffres ne prouvent pas** : ils décrivent des signaux synthétiques (parole simulée, scènes de luminosité
simulées). Les seuils par défaut (`SyncConfig`) sont des valeurs initiales ; la calibration terrain sur de vrais rushes
reste à faire (voir `docs/SYNC.md`, section « Rushes nécessaires »).
Objectif d'acceptation : erreur de décalage ≤ {TOL * 1000:.0f} ms (une image à 25 i/s), aucun faux « ok ».
"""
    Path(args.out).write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"écrit : {args.out} ({time.perf_counter() - t_start:.0f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
