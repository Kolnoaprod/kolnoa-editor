"""Synchronisation audio : décalage, dérive, discontinuités, confiance.

Principe : (1) recherche grossière globale par enveloppe d'énergie (ou autour d'un indice) ; (2) affinage par
corrélation de phase sur des fenêtres réparties dans le clip ; (3) droite robuste décalage/temps (dérive) ;
(4) confiance composée de la netteté des pics, de la couverture et de l'accord entre fenêtres.

Convention : t_groupe = offset + t_angle × (1 + dérive_ppm × 1e-6). Rien n'est inventé : sans corrélation nette le
résultat est « impossible », sans accord entre fenêtres « incertain » ou « discontinu ».
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..ffmpeg import CancelToken
from .config import SyncConfig
from .correlate import gcc_phat, ncc

STATUSES = ("ok", "incertain", "discontinu", "impossible")


@dataclass
class WindowResult:
    t_angle: float          # centre de la fenêtre dans l'angle (s)
    offset: float | None    # décalage mesuré (s) ou None
    psr: float
    silent: bool = False
    valid: bool = False
    inlier: bool = False


@dataclass
class Segment:
    t_start: float          # angle (s)
    t_end: float
    offset: float
    drift_ppm: float
    windows: int


@dataclass
class SyncEstimate:
    status: str
    confidence: float
    offset: float = 0.0
    drift_ppm: float = 0.0
    drift_measured: bool = False
    method: str = "audio"
    windows: list[WindowResult] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    elapsed_s: float = 0.0


def _impossible(note: str, t0: float, **metrics) -> SyncEstimate:
    return SyncEstimate("impossible", 0.0, notes=[note], metrics=metrics, elapsed_s=time.perf_counter() - t0)


def _theil_sen(t: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """(pente, ordonnée à l'origine) robustes ; pente = dérive en s/s."""
    if len(t) < 2:
        return 0.0, float(np.median(y))
    i, j = np.triu_indices(len(t), 1)
    dt = t[j] - t[i]
    ok = np.abs(dt) > 1e-9
    slope = float(np.median((y[j][ok] - y[i][ok]) / dt[ok])) if ok.any() else 0.0
    return slope, float(np.median(y - slope * t))


def _stretch(x: np.ndarray, drift_ppm: float) -> np.ndarray:
    """Remet une fenêtre de l'angle à l'échelle de temps du groupe (compense la dérive d'horloge) : sans cela, sur
    une fenêtre de 20 s à 1000 ppm, le décalage glisse de 20 ms et le pic de corrélation s'étale."""
    if abs(drift_ppm) < 1e-9:
        return x
    k = np.arange(len(x)) / (1.0 + drift_ppm * 1e-6)
    return np.interp(k, np.arange(len(x)), x.astype(np.float64)).astype(np.float32)


def _measure(ref, starts, sig_win, rms, w, cfg, plan, drift_ppm, cancel) -> list[WindowResult]:
    """Mesure chaque fenêtre. `plan(s)` donne le temps de groupe attendu du début de la fenêtre ; en cas de
    fenêtre valide, il est mis à jour par `plan.update`."""
    results: list[WindowResult] = []
    med = float(np.median(rms)) if len(rms) else 0.0
    S = cfg.wide_search_s
    for s, x, r in zip(starts, sig_win, rms):
        if cancel is not None:
            cancel.raise_if_cancelled()
        tc = s + w / (2.0 * (1.0 + drift_ppm * 1e-6))
        if med <= 0 or r < cfg.silence_ratio * med:
            results.append(WindowResult(tc, None, 0.0, silent=True))
            continue
        g_exp = plan(s)
        seg = ref.read(g_exp - S, w * (1.0 + drift_ppm * 1e-6) + 2 * S)
        peak = gcc_phat(seg, _stretch(x, drift_ppm), ref.rate, expected_lag_s=S, search_s=S, band_hz=cfg.band_hz)
        if peak is None:
            results.append(WindowResult(tc, None, 0.0))
            continue
        g0 = g_exp - S + peak.lag_s                   # temps de groupe du début de fenêtre
        off = g0 + w / 2.0 - tc                       # décalage t_groupe - t_angle au centre de la fenêtre
        valid = peak.psr >= cfg.min_psr
        results.append(WindowResult(tc, off, peak.psr, valid=valid))
        if valid:
            plan.update(s, g0)
    return results


class _Plan:
    """Prévision du début de fenêtre dans le temps de groupe : suit la dernière fenêtre valide (passe 1) ou la
    droite ajustée (passe 2)."""

    def __init__(self, coarse: float, line: tuple[float, float] | None = None):
        self.coarse, self.line = coarse, line
        self.last: tuple[float, float] | None = None

    def __call__(self, s: float) -> float:
        if self.line is not None:
            icpt, drift = self.line
            return icpt + s * (1.0 + drift)
        if self.last is None:
            return s + self.coarse
        ls, lg = self.last
        return lg + (s - ls)

    def update(self, s: float, g0: float) -> None:
        self.last = (s, g0)


def _analyse(results: list[WindowResult], w: float, cfg: SyncConfig, metrics: dict, t0: float) -> SyncEstimate:
    scored = [r for r in results if not r.silent]
    valid = [r for r in results if r.valid]
    metrics = {**metrics, "windows": len(results), "silent": len(results) - len(scored), "valid": len(valid)}
    if not scored:
        return _impossible("audio de l'angle silencieux", t0, **metrics)
    need = min(cfg.min_windows, len(results))
    if not valid:
        est = _impossible("aucune fenêtre ne présente de pic net", t0, **metrics)
        est.windows = results
        return est

    # segments (discontinuités) puis droite robuste du segment dominant
    t = np.array([r.t_angle for r in valid])
    y = np.array([r.offset for r in valid])
    runs: list[list[int]] = [[0]]
    for i in range(1, len(valid)):
        dt = t[i] - t[i - 1]
        if abs(y[i] - y[i - 1]) > cfg.jump_tol_s + cfg.max_drift_ppm * 1e-6 * dt:
            runs.append([])
        runs[-1].append(i)
    notes: list[str] = []
    segments: list[Segment] = []
    for run in runs:
        tt, yy = t[run], y[run]
        if len(run) >= 3 and tt[-1] - tt[0] >= 60.0:
            sl, ic = _theil_sen(tt, yy)
        else:
            sl, ic = 0.0, float(np.median(yy))
        segments.append(Segment(float(tt[0] - w / 2), float(tt[-1] + w / 2), ic, sl * 1e6, len(run)))
    dom = max(range(len(runs)), key=lambda k: len(runs[k]))
    idx = runs[dom]
    tt, yy = t[idx], y[idx]
    drift_measured = len(idx) >= 3 and (tt[-1] - tt[0]) >= 60.0
    if drift_measured:
        slope, icpt = _theil_sen(tt, yy)
        inl = np.abs(yy - (icpt + slope * tt)) <= cfg.inlier_tol_s
        if inl.sum() >= 3:
            slope, icpt = np.polyfit(tt[inl], yy[inl], 1)
            inl = np.abs(yy - (icpt + slope * tt)) <= cfg.inlier_tol_s
    else:
        slope, icpt = 0.0, float(np.median(yy))
        inl = np.abs(yy - icpt) <= cfg.inlier_tol_s
        notes.append("dérive non mesurable (clip court ou peu de fenêtres valides) : fixée à 0")
    drift_ppm = float(slope * 1e6)
    suspicious = abs(drift_ppm) > cfg.max_drift_ppm
    if suspicious:
        notes.append(f"dérive {drift_ppm:.0f} ppm suspecte (> {cfg.max_drift_ppm:.0f}) : ignorée, à vérifier")
        slope, icpt, drift_ppm = 0.0, float(np.median(yy)), 0.0
        inl = np.abs(yy - icpt) <= cfg.inlier_tol_s
    inlier_idx = {idx[k] for k in range(len(idx)) if inl[k]}
    for k, r in enumerate(valid):
        r.inlier = k in inlier_idx
    n_inl = len(inlier_idx)

    # confiance (composition documentée dans docs/SYNC.md)
    psrs = [valid[k].psr for k in inlier_idx] or [valid[i].psr for i in idx]
    c_psr = float(np.clip((np.median(psrs) - cfg.min_psr) / max(cfg.good_psr - cfg.min_psr, 1e-9), 0.0, 1.0))
    c_psr = max(c_psr, 0.05)
    c_cov = min(1.0, (len(valid) / len(scored)) / cfg.coverage_good)
    c_agree = n_inl / len(valid)
    confidence = float((c_psr * c_cov * c_agree) ** (1 / 3))
    if suspicious:
        confidence *= 0.5
    if len(valid) < need:
        confidence = min(confidence, cfg.uncertain_confidence - 0.01)
        notes.append(f"seulement {len(valid)} fenêtre(s) valide(s) sur {need} requises")
    unmatched = [r for r in scored if not r.valid]
    if unmatched:
        spans = ", ".join(f"{r.t_angle - w / 2:.0f}-{r.t_angle + w / 2:.0f} s" for r in unmatched[:6])
        notes.append(f"{len(unmatched)} fenêtre(s) sans correspondance dans l'audio maître ({spans}) : "
                     "clip plus long que l'enregistrement, saut au-delà de la portée de recherche ou audio différent")
    if len(runs) > 1:
        status = "discontinu"
        notes.append(f"{len(runs)} segments au décalage différent : clip discontinu, à découper")
    elif confidence >= cfg.ok_confidence:
        status = "ok"
    elif confidence >= cfg.uncertain_confidence:
        status = "incertain"
    else:
        status = "impossible"
        notes.append("accord entre fenêtres ou netteté des pics insuffisants")
    metrics.update({"inliers": n_inl, "median_psr": round(float(np.median(psrs)), 2),
                    "c_psr": round(c_psr, 3), "c_cov": round(c_cov, 3), "c_agree": round(c_agree, 3)})
    return SyncEstimate(status, round(confidence, 4), offset=float(icpt), drift_ppm=drift_ppm,
                        drift_measured=drift_measured and not suspicious, windows=results, segments=segments,
                        notes=notes, metrics=metrics, elapsed_s=time.perf_counter() - t0)


def estimate_audio_sync(ref, sig, cfg: SyncConfig | None = None, hint: tuple[float, float] | None = None,
                        cancel: CancelToken | None = None) -> SyncEstimate:
    """Aligne `sig` (angle) sur `ref` (audio maître du groupe).

    `hint` = (décalage attendu, tolérance) en secondes : indice (métadonnées ou main) qui restreint la recherche
    grossière. Il n'est jamais pris pour le résultat.
    """
    cfg = cfg or SyncConfig()
    t0 = time.perf_counter()
    dur = float(sig.duration)
    if dur < 2.0:
        return _impossible("clip trop court (< 2 s) pour une synchro fiable", t0)

    # 1. recherche grossière globale (enveloppes d'énergie)
    env_ref, env_sig = ref.envelope(cfg.env_rate), sig.envelope(cfg.env_rate)
    if hint is not None:
        res = ncc(env_ref, env_sig, int(round(hint[0] * cfg.env_rate)), int(round(hint[1] * cfg.env_rate)))
    else:
        res = ncc(env_ref, env_sig)
    if res is None or res[1] < cfg.min_env_psr:
        return _impossible("aucune correspondance sonore globale (enveloppes sans pic net)", t0,
                           env_psr=0.0 if res is None else round(res[1], 2))
    coarse, env_psr = res[0] / cfg.env_rate, res[1]
    base = {"env_psr": round(env_psr, 2), "coarse_offset": round(coarse, 4)}

    # 2. fenêtres d'affinage, passe 1 (sans compensation de dérive)
    w = min(cfg.window_s, dur)
    n = max(1, min(cfg.max_windows, int(dur // w)))
    starts = [0.0] if n == 1 else [float(v) for v in np.linspace(0.0, dur - w, n)]
    sig_win = [sig.read(s, w) for s in starts]
    rms = np.array([float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) for x in sig_win])
    r1 = _measure(ref, starts, sig_win, rms, w, cfg, _Plan(coarse), 0.0, cancel)
    est = _analyse(r1, w, cfg, base, t0)

    # 3. passe 2 : si une dérive significative est mesurée, on recompense chaque fenêtre et on réajuste
    if est.drift_measured and abs(est.drift_ppm) >= cfg.drift_compensation_ppm:
        line = (est.offset, est.drift_ppm * 1e-6)
        r2 = _measure(ref, starts, sig_win, rms, w, cfg, _Plan(coarse, line), est.drift_ppm, cancel)
        est2 = _analyse(r2, w, cfg, {**base, "pass": 2}, t0)
        if est2.metrics.get("valid", 0) >= est.metrics.get("valid", 0) and est2.status != "impossible":
            est2.notes.append(f"dérive compensée sur les fenêtres (passe 2, {est.drift_ppm:.0f} ppm de départ)")
            est = est2
    est.elapsed_s = time.perf_counter() - t0
    return est
