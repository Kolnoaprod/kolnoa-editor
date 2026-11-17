"""Pyramide de crêtes audio pour dessiner des formes d'onde à n'importe quel zoom sans relire le son. Sans Qt."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

BIN_S = 0.02                       # un pic par 20 ms au niveau 0


class PeakPyramid:
    """Niveau 0 : 1 crête (0..255) par 20 ms ; niveau k : maximum de 2**k crêtes du niveau 0."""

    def __init__(self, base: np.ndarray, complete: bool = True, revision: int = 0):
        self.complete = complete          # False : calcul en cours, seule la partie déjà lue est remplie
        self.revision = revision          # change à chaque nouvelle tranche (invalide les images en cache)
        self.levels = [np.asarray(base, dtype=np.uint8)]
        while len(self.levels[-1]) > 64:
            a = self.levels[-1]
            n = len(a) // 2 * 2
            self.levels.append(np.maximum(a[:n:2], a[1:n:2]))

    def covers(self, t: float) -> bool:
        """Les crêtes sont-elles disponibles jusqu'à la seconde `t` (ou le calcul est-il terminé) ?"""
        return self.complete or t <= self.duration

    @property
    def duration(self) -> float:
        return len(self.levels[0]) * BIN_S

    def columns(self, t0: float, seconds_per_px: float, count: int) -> np.ndarray:
        """Amplitude (0..255) de `count` colonnes de pixels à partir de `t0` secondes. Hors média : 0."""
        if count <= 0:
            return np.zeros(0, dtype=np.uint8)
        level = int(max(0.0, np.log2(max(seconds_per_px, BIN_S) / BIN_S)))      # niveau dont le pic couvre ≲ 1 pixel
        level = min(level, len(self.levels) - 1)
        arr, step = self.levels[level], BIN_S * (2 ** level)
        idx = ((t0 + np.arange(count) * seconds_per_px) / step).astype(np.int64)
        out = np.zeros(count, dtype=np.uint8)
        ok = (idx >= 0) & (idx < len(arr))
        out[ok] = arr[idx[ok]]
        return out


def load_peaks(media_path: str | Path, max_seconds: float = 4 * 3600) -> PeakPyramid | None:
    """Décode l'audio (mono 8 kHz) avec ffmpeg et construit la pyramide. None si le média n'a pas d'audio ou si ffmpeg échoue.
    N'écrit rien et ne modifie jamais le média. À appeler hors du fil de l'interface."""
    try:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(media_path), "-t", str(max_seconds), "-vn", "-ac", "1",
                              "-ar", "8000", "-f", "f32le", "-"], stdin=subprocess.DEVNULL, capture_output=True,
                             timeout=600).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    bins = samples_to_bins(np.frombuffer(raw, dtype=np.float32))
    return PeakPyramid(bins) if len(bins) else None


SAMPLE_RATE = 8000
BIN_SAMPLES = int(SAMPLE_RATE * BIN_S)


def samples_to_bins(x: np.ndarray) -> np.ndarray:
    """Échantillons mono 8 kHz (float) → une crête 0..255 par tranche de 20 ms (les échantillons en trop sont ignorés)."""
    n = len(x) // BIN_SAMPLES
    if n == 0:
        return np.zeros(0, dtype=np.uint8)
    pk = np.abs(x[: n * BIN_SAMPLES]).reshape(n, BIN_SAMPLES).max(axis=1)
    return np.minimum(255, np.round(pk * 255)).astype(np.uint8)
