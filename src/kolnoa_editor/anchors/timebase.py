"""Base de temps des ancres (A1-2) : tout en ENTIERS (microsecondes), arrondi « demi vers le haut » exact, sans flottant.

Un plan lit sa source à vitesse 1 : l'image `f` d'un plan commence à la microseconde source
    us(f) = src_in_us + rdiv((f − start)·10⁶, fps)        (fps = cadence ENTIÈRE de la timeline)
et une microseconde source `a` tombe dans l'image `start + ⌊(a − src_in_us + 1)·fps / 10⁶⌋` (voir `us_to_offset`).
L'aller-retour image → µs → image est exact pour toute cadence entière ≤ 1000 i/s (écart d'arrondi ≤ 1 µs ≪ une image)."""

from __future__ import annotations

US = 1_000_000
BIAS_US = 1      # absorbe l'arrondi cumulé : ancre (±½ µs) + `src_in` arrondi à 6 décimales (±½ µs) ≤ 1 µs


def rdiv(n: int, d: int) -> int:
    """⌊n/d + ½⌋ en entiers purs (d > 0) : arrondi au plus proche, les demis vers le haut, y compris pour n négatif."""
    if d <= 0:
        raise ValueError("diviseur non positif")
    return (2 * n + d) // (2 * d)


def seconds_to_us(seconds: float) -> int:
    """Secondes (déjà arrondies à 6 décimales par le moteur) → µs entières."""
    return round(seconds * US)


def frames_to_us(frames: int, fps: int) -> int:
    """Durée de `frames` images en µs (arrondi au plus proche)."""
    return rdiv(frames * US, fps)


def offset_to_us(offset_frames: int, fps: int) -> int:
    """Décalage (en images, depuis le début du plan) → µs de source."""
    return rdiv(offset_frames * US, fps)


def us_to_offset(delta_us: int, fps: int) -> int:
    """µs depuis l'entrée du plan → numéro d'image (plancher) ; le biais de ½ µs rend l'aller-retour exact avec `offset_to_us`."""
    return ((delta_us + BIAS_US) * fps) // US


def us_to_end_offset(delta_us: int, fps: int) -> int:
    """µs depuis l'entrée → borne de fin EXCLUE en images : plafond avec la même tolérance (une image entamée compte)."""
    return -(-((delta_us - BIAS_US) * fps) // US)
