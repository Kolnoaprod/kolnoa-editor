"""Estimation du tempo à partir des transitoires du signal audio."""

from dataclasses import asdict, dataclass
from uuid import uuid4

import numpy as np

from ..sync.sources import FileSource
from ..timeline import AddMarker, Transaction
from ..timeline.model import Marker


@dataclass(frozen=True)
class Rhythm:
    bpm: float | None
    beats: tuple[float, ...]
    confidence: float


def estimate_rhythm(envelope, rate: int = 100) -> Rhythm:
    signal = np.asarray(envelope, dtype=float)
    uncertain = Rhythm(None, (), 0.0)
    if len(signal) < rate * 4:
        return uncertain
    onset = np.maximum(np.diff(signal, prepend=signal[0]), 0)
    energy = float(np.dot(onset, onset))
    if energy < 1e-9:
        return uncertain
    lags = range(round(rate * 60 / 190), round(rate * 60 / 65) + 1)
    scores = [float(np.dot(onset[lag:], onset[:-lag])) / energy for lag in lags]
    period = lags[int(np.argmax(scores))]
    confidence = max(scores)
    if confidence < .22:
        return uncertain
    peak = onset > max(float(onset.max()) * .18, float(np.mean(onset)) * 3)
    peaks = np.flatnonzero(peak[1:-1] & (onset[1:-1] >= onset[:-2]) & (onset[1:-1] > onset[2:])) + 1
    phase = int(np.argmax(np.bincount(peaks % period, weights=onset[peaks], minlength=period)))
    beats = []
    for center in range(phase, len(signal), period):
        candidates = peaks[np.abs(peaks - center) <= max(2, period // 7)]
        if candidates.size:
            beats.append(float(candidates[np.argmax(onset[candidates])]) / rate)
    if len(beats) < 4:
        return uncertain
    return Rhythm(60 * rate / period, tuple(beats), confidence)


def analyze_music(path, *, cancel=None) -> Rhythm:
    source = FileSource(path, cancel=cancel)
    return estimate_rhythm(source.envelope(100), 100)


def beat_markers(timeline, rhythm: Rhythm, start: int, end: int) -> Transaction:
    occupied = {marker.frame for marker in timeline.markers if marker.kind == "beat"}
    markers = []
    for time in rhythm.beats:
        frame = start + round(time * timeline.fps)
        if frame < end and frame not in occupied:
            markers.append(AddMarker(asdict(Marker(uuid4().hex, frame, "beat", "Battement détecté"))))
            occupied.add(frame)
    return Transaction(markers, "Repères musicaux")
