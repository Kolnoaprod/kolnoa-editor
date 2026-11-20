"""Analyse locale (aucune API, aucun coût) : parole/silence par `silencedetect` de ffmpeg.

L'analyse est séparée du reste : `RushInfo` est une donnée simple, donc le planificateur se teste sans média."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..ffmpeg import CancelToken, ffmpeg_bin, run


@dataclass(frozen=True)
class Moment:
    start: float            # secondes dans le rush
    end: float
    score: float = 1.0      # 0..1 : intérêt estimé (v0 : parole = 1, ambiance = 0.4)
    kind: str = "speech"    # speech | ambient

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass
class RushInfo:
    media: str                          # chemin relatif au dossier mariage
    duration: float
    start_time: str | None = None       # ISO local naïf (ordre chronologique)
    group: str = ""                     # 1er dossier : « séquence » (MAIRIE, SOIREE…)
    device: str = ""
    has_audio: bool = True
    moments: list[Moment] = field(default_factory=list)


_SIL = re.compile(r"silence_(start|end): (-?[\d.]+)")


def analyze_silences(path: Path, duration: float, noise_db: float = -35.0, min_silence: float = 0.6,
                     runner=None, *, cancel: CancelToken | None = None) -> list[tuple[float, float]]:
    """Plages SILENCIEUSES [(début, fin)] d'un fichier, par ffmpeg. `runner` injectable pour les tests."""
    cmd = [ffmpeg_bin() if runner is None else "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
           f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"]
    p = run(cmd, cancel=cancel, check=False, timeout=max(120, 2 * duration)) if runner is None else runner(cmd, capture_output=True, text=True)
    out, cur = [], None
    for kind, val in _SIL.findall(p.stderr or ""):
        v = max(0.0, float(val))
        if kind == "start":
            cur = v
        elif cur is not None:
            out.append((cur, min(v, duration)))
            cur = None
    if cur is not None:
        out.append((cur, duration))
    return out


def speech_moments(duration: float, silences: list[tuple[float, float]], min_len: float = 0.8) -> list[Moment]:
    """Complément des silences = moments sonores. Un rush sans aucun silence détecté = un seul moment."""
    out, pos = [], 0.0
    for a, b in sorted(silences):
        if a - pos >= min_len:
            out.append(Moment(round(pos, 3), round(a, 3), 1.0, "speech"))
        pos = max(pos, b)
    if duration - pos >= min_len:
        out.append(Moment(round(pos, 3), round(duration, 3), 1.0, "speech"))
    return out
