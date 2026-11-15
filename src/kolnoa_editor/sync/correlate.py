"""Corrélations pures (numpy) : enveloppe d'énergie et corrélation de phase (GCC-PHAT).

Convention de décalage (partout) : un même événement a lieu à `t_ref` dans la référence et à `t_sig` dans le
signal ; `lag = t_ref - t_sig`. Un lag positif signifie que le signal démarre « plus tôt » que la référence.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Peak:
    lag_s: float        # décalage estimé (secondes), interpolé sous l'échantillon
    psr: float          # (pic - moyenne) / écart-type du reste de la plage : « à quel point il se détache »
    value: float        # hauteur brute du pic


def _next_pow2(n: int) -> int:
    return 1 << max(1, (n - 1).bit_length())


def _peak(cc: np.ndarray, lags0: int, fs: float, lo: int, hi: int, exclude: int) -> Peak | None:
    """Cherche le pic de `cc` entre les indices [lo, hi) ; `lags0` = lag (en échantillons) de l'indice 0."""
    lo, hi = max(lo, 0), min(hi, len(cc))
    if hi - lo < 8:
        return None
    seg = cc[lo:hi]
    i = int(np.argmax(seg))
    peak = float(seg[i])
    mask = np.ones(len(seg), dtype=bool)
    mask[max(0, i - exclude): i + exclude + 1] = False
    rest = seg[mask]
    if len(rest) < 8:
        return None
    sd = float(rest.std())
    psr = (peak - float(rest.mean())) / sd if sd > 1e-12 else 0.0
    frac = 0.0                      # interpolation parabolique
    if 0 < i < len(seg) - 1:
        a, b, c = float(seg[i - 1]), float(seg[i]), float(seg[i + 1])
        den = a - 2 * b + c
        if abs(den) > 1e-18:
            frac = 0.5 * (a - c) / den
    return Peak(((lo + i + frac) + lags0) / fs, psr, peak)


def gcc_phat(ref: np.ndarray, sig: np.ndarray, fs: float, expected_lag_s: float = 0.0,
             search_s: float | None = None, band_hz: tuple[float, float] | None = (150.0, 3500.0),
             exclude_s: float = 0.01) -> Peak | None:
    """Corrélation de phase entre `ref` et `sig` (même fréquence d'échantillonnage `fs`).

    Renvoie le meilleur décalage dans [expected ± search_s] (toute la plage si `search_s` est None).
    """
    ref = np.asarray(ref, dtype=np.float64)
    sig = np.asarray(sig, dtype=np.float64)
    if len(ref) < 16 or len(sig) < 16:
        return None
    nfft = _next_pow2(len(ref) + len(sig))
    X = np.fft.rfft(ref - ref.mean(), nfft)
    Y = np.fft.rfft(sig - sig.mean(), nfft)
    R = X * np.conj(Y)
    mag = np.abs(R)
    R = R / (mag + 1e-12 * (mag.max() + 1e-30))
    if band_hz is not None:
        freqs = np.fft.rfftfreq(nfft, 1.0 / fs)
        R[(freqs < band_hz[0]) | (freqs > band_hz[1])] = 0.0
    cc = np.fft.irfft(R, nfft)
    # cc[k] = somme_n ref[n+k]*sig[n] ; k de -(len(sig)-1) à len(ref)-1
    cc = np.concatenate([cc[nfft - (len(sig) - 1):], cc[:len(ref)]])
    lags0 = -(len(sig) - 1)
    if search_s is None:
        lo, hi = 0, len(cc)
    else:
        center = int(round(expected_lag_s * fs)) - lags0
        w = int(round(search_s * fs))
        lo, hi = center - w, center + w + 1
    return _peak(cc, lags0, fs, lo, hi, max(1, int(exclude_s * fs)))


def energy_envelope(x: np.ndarray, fs: float, env_rate: int = 100) -> np.ndarray:
    """Enveloppe d'énergie à `env_rate` Hz : log de l'énergie de la dérivée du signal (écarte les graves),
    privée de sa tendance lente (moyenne mobile ~2 s) pour que seuls les événements comptent."""
    x = np.asarray(x, dtype=np.float64)
    block = max(1, int(round(fs / env_rate)))
    n = (len(x) // block) * block
    if n == 0:
        return np.zeros(0)
    d = np.diff(x[:n], prepend=x[0])
    e = np.log1p((d.reshape(-1, block) ** 2).mean(axis=1) * 1e4)
    return detrend(e, int(2 * env_rate))


def detrend(e: np.ndarray, win: int) -> np.ndarray:
    if len(e) == 0:
        return e
    win = max(1, min(win, len(e)))
    k = np.ones(win) / win
    pad = np.pad(e, (win // 2, win - 1 - win // 2), mode="edge")
    return e - np.convolve(pad, k, mode="valid")


def ncc_curve(ref: np.ndarray, sig: np.ndarray) -> tuple[np.ndarray, int, int, int] | None:
    """Courbe de corrélation croisée normalisée de `sig` dans `ref`.

    Renvoie (cc, lags0, lo, hi) : cc[i] est la corrélation au décalage i + lags0 (indice dans ref - indice dans sig) ;
    seuls les indices [lo, hi) comptent (là où `sig` recouvre au moins à moitié `ref`).
    """
    if len(sig) < 8 or len(ref) < 8:
        return None
    s = sig - sig.mean()
    sn = float(np.sqrt((s ** 2).sum()))
    if sn < 1e-9:
        return None
    nfft = _next_pow2(len(ref) + len(sig))
    cc = np.fft.irfft(np.fft.rfft(ref, nfft) * np.conj(np.fft.rfft(s, nfft)), nfft)
    cc = np.concatenate([cc[nfft - (len(sig) - 1):], cc[:len(ref)]])      # lag k : -(len(sig)-1) .. len(ref)-1
    lags0 = -(len(sig) - 1)
    # normalisation par l'énergie locale de ref (fenêtre glissante de la longueur de sig)
    c1 = np.concatenate([[0.0], np.cumsum(ref)])
    c2 = np.concatenate([[0.0], np.cumsum(ref ** 2)])
    n = len(sig)
    lag = np.arange(lags0, len(ref))
    a = np.clip(lag, 0, len(ref))
    b = np.clip(lag + n, 0, len(ref))
    cnt = np.maximum(b - a, 1)
    sm, sq = c1[b] - c1[a], c2[b] - c2[a]
    local = np.sqrt(np.maximum(sq - sm * sm / cnt, 1e-12))
    full = (b - a) >= n * 0.5                      # au moins la moitié du signal doit recouvrir la référence
    cc = np.where(full, cc / (local * sn), -1.0)
    if not full.any():
        return None
    # seuls les décalages où `sig` recouvre au moins à moitié `ref` comptent (le reste fausserait le bruit de fond)
    return cc, lags0, int(np.argmax(full)), len(full) - int(np.argmax(full[::-1]))


def ncc(ref: np.ndarray, sig: np.ndarray, expected: int = 0, search: int | None = None,
        exclude: int = 5) -> tuple[int, float, float] | None:
    """Meilleur décalage entier (lag, psr, valeur), éventuellement borné à expected ± search."""
    curve = ncc_curve(ref, sig)
    if curve is None:
        return None
    cc, lags0, lo, hi = curve
    if search is not None:
        lo = max(lo, expected - lags0 - search)
        hi = min(hi, expected - lags0 + search + 1)
    p = _peak(cc, lags0, 1.0, lo, hi, max(2, exclude))
    if p is None:
        return None
    return int(round(p.lag_s)), p.psr, p.value


def competing_peak(env_ref: np.ndarray, env_sig: np.ndarray, best_lag: int, exclusion: int) -> float | None:
    """Hauteur relative (0-1+) du meilleur pic de corrélation d'enveloppes situé à plus de `exclusion` échantillons du
    pic retenu, divisée par la hauteur de ce dernier. Proche de 1 : deux alignements aussi plausibles (musique ou boucle
    jouée plusieurs fois, par exemple) : le résultat est ambigu même si le pic retenu est net."""
    curve = ncc_curve(env_ref, env_sig)
    if curve is None:
        return None
    cc, lags0, lo, hi = curve
    seg = cc[lo:hi].copy()
    i0 = best_lag - lags0 - lo
    if not (0 <= i0 < len(seg)) or seg[i0] <= 0:
        return None
    ref_val = float(seg[i0])
    seg[max(0, i0 - exclusion): i0 + exclusion + 1] = -1.0
    other = float(seg.max()) if len(seg) else -1.0
    return max(other, 0.0) / ref_val
