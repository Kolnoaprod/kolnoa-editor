"""Sélection technique par plages pour le mode Film condensé."""

from dataclasses import dataclass
from math import floor
from pathlib import PurePosixPath

from .analysis import RushInfo
from .planner import Shot, StyleParams
from .visual import Sample


@dataclass(frozen=True)
class MediaScope:
    media: frozenset[str] | None = None

    def includes(self, rush: RushInfo) -> bool:
        return self.media is None or rush.media in self.media


def source_folder(rush: RushInfo) -> str:
    return PurePosixPath(rush.media).parent.as_posix()


@dataclass(frozen=True)
class BalancedThresholds:
    min_sharpness: float = 12.0
    max_clipped_fraction: float = 0.70
    max_shot_seconds: float = 6.0


def select_portions(rush: RushInfo, samples: list[Sample], slot_seconds: float, style: StyleParams,
                    thresholds: BalancedThresholds) -> tuple[list[Shot], list[str]]:
    """Sépare les plages acceptées par leurs mesures et garde le meilleur extrait de chacune."""
    runs: list[list[Sample]] = []
    current: list[Sample] = []
    notes: list[str] = []
    for sample in samples:
        time = sample.t_angle
        reasons = []
        if sample.sharp < thresholds.min_sharpness:
            reasons.append("netteté faible")
        if sample.bright > thresholds.max_clipped_fraction:
            reasons.append("hautes lumières écrêtées")
        if sample.dark > thresholds.max_clipped_fraction:
            reasons.append("ombres bouchées")
        if reasons:
            notes.append(f"{max(0, time - slot_seconds / 2):.1f}–{min(rush.duration, time + slot_seconds / 2):.1f} s : " + ", ".join(reasons))
        if current and (reasons or time - current[-1].t_angle > 1.5 * slot_seconds):
            runs.append(current)
            current = []
        if not reasons:
            current.append(sample)
    if current:
        runs.append(current)

    shots = []
    max_seconds = min(style.shot_max, thresholds.max_shot_seconds)
    width = max(1, floor(max_seconds / slot_seconds))
    for run in runs:
        n = min(width, len(run))
        if n * slot_seconds < style.shot_min:
            continue

        def merit(sample: Sample) -> float:
            speech = any(m.start <= sample.t_angle < m.end for m in rush.moments)
            return 1 - sample.dark - sample.bright + min(1, sample.sharp / 200) + 0.5 * speech

        best = max(range(len(run) - n + 1), key=lambda i: sum(merit(s) for s in run[i:i+n]))
        start = max(0.0, run[best].t_angle - slot_seconds / 2)
        end = min(rush.duration, run[best + n - 1].t_angle + slot_seconds / 2, start + max_seconds)
        if end - start >= style.shot_min:
            shots.append(Shot(rush.media, start, end - start, "Film condensé"))
    return shots, notes
