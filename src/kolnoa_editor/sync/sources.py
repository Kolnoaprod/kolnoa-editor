"""Sources audio : un tableau en mémoire (tests) ou un fichier lu par ffmpeg (production).

Les deux exposent la même interface : durée, lecture d'une fenêtre [t0, t0+dur] (complétée par du silence hors
du fichier) et enveloppe d'énergie complète. Un fichier long (enregistreur WAV de plusieurs heures) n'est jamais
chargé en entier : l'enveloppe est calculée par blocs, l'affinage ne relit que de courtes fenêtres.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np

from ..ffmpeg import CancelToken, ffmpeg_bin, run
from ..probe import probe
from .correlate import detrend, energy_envelope


class ArraySource:
    def __init__(self, samples: np.ndarray, rate: int):
        self.samples = np.asarray(samples, dtype=np.float32)
        self.rate = rate
        self.duration = len(self.samples) / rate

    def read(self, t0: float, dur: float) -> np.ndarray:
        n = int(round(dur * self.rate))
        i0 = int(round(t0 * self.rate))
        out = np.zeros(n, dtype=np.float32)
        a, b = max(i0, 0), min(i0 + n, len(self.samples))
        if b > a:
            out[a - i0: b - i0] = self.samples[a:b]
        return out

    def envelope(self, env_rate: int = 100) -> np.ndarray:
        return energy_envelope(self.samples, self.rate, env_rate)


class FileSource:
    """Audio d'un fichier (vidéo ou son) ramené en mono à `rate` Hz par ffmpeg."""

    def __init__(self, path: Path | str, rate: int = 8000, cancel: CancelToken | None = None,
                 tmp_dir: Path | None = None):
        self.path = Path(path)
        self.rate = rate
        self.cancel = cancel
        self.tmp_dir = Path(tmp_dir) if tmp_dir else None
        info = probe(self.path)
        if not info.has_audio:
            raise ValueError(f"{self.path.name}: aucune piste audio")
        self.duration = info.duration

    def _decode(self, t0: float, dur: float | None, out: Path) -> None:
        cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-v", "error"]
        if t0 > 0:
            cmd += ["-ss", f"{t0:.6f}"]
        cmd += ["-i", str(self.path)]
        if dur is not None:
            cmd += ["-t", f"{dur:.6f}"]
        cmd += ["-vn", "-ac", "1", "-ar", str(self.rate), "-f", "f32le", str(out)]
        run(cmd, cancel=self.cancel)

    def _tmp(self) -> Path:
        fd, name = tempfile.mkstemp(suffix=".f32", dir=self.tmp_dir)
        os.close(fd)
        return Path(name)

    def read(self, t0: float, dur: float) -> np.ndarray:
        n = int(round(dur * self.rate))
        out = np.zeros(n, dtype=np.float32)
        start = max(t0, 0.0)
        end = min(t0 + dur, self.duration)
        if end <= start:
            return out
        tmp = self._tmp()
        try:
            self._decode(start, end - start, tmp)
            data = np.fromfile(tmp, dtype=np.float32)
        finally:
            tmp.unlink(missing_ok=True)
        i0 = int(round((start - t0) * self.rate))
        m = min(len(data), n - i0)
        if m > 0:
            out[i0: i0 + m] = data[:m]
        return out

    def envelope(self, env_rate: int = 100) -> np.ndarray:
        tmp = self._tmp()
        try:
            self._decode(0.0, None, tmp)
            data = np.memmap(tmp, dtype=np.float32, mode="r")
            block = max(1, int(round(self.rate / env_rate)))
            step = block * env_rate * 60               # blocs d'environ une minute, alignés sur l'enveloppe
            parts = []
            for i in range(0, len(data), step):
                x = np.asarray(data[i: i + step], dtype=np.float64)
                n = (len(x) // block) * block
                if n == 0:
                    continue
                d = np.diff(x[:n], prepend=x[0])
                parts.append(np.log1p((d.reshape(-1, block) ** 2).mean(axis=1) * 1e4))
            del data
        finally:
            tmp.unlink(missing_ok=True)
        return detrend(np.concatenate(parts), 2 * env_rate) if parts else np.zeros(0)
