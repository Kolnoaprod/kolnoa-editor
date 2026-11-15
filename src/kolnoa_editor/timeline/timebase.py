"""Base de temps unique : la timeline compte en images entières, la source en secondes.

Tous les passages secondes <-> images passent par ici, pour qu'aucune dérive flottante ne
s'accumule et que les raccords restent exacts à l'image.
"""

from __future__ import annotations


def seconds_to_frames(seconds: float, fps: int) -> int:
    """Secondes -> images (arrondi à l'image la plus proche, jamais 0 pour une durée > 0)."""
    return round(seconds * fps)


def frames_to_seconds(frames: int, fps: int) -> float:
    """Images -> secondes, arrondies à la microseconde (stable dans le JSON)."""
    return round(frames / fps, 6)


def duration_frames(seconds: float, fps: int) -> int:
    """Durée d'un plan en images : même règle que le moteur de rendu (au moins 1 image)."""
    return max(1, round(seconds * fps))
