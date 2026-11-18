"""Traduction d'une pile de réglages de couleur en filtres ffmpeg (rendu et aperçu : même chaîne).

Ordre fixe, documenté : pour chaque réglage de la pile (global, angle, plan) :
  1. LUT 3D (tétraédrique), mélangée à l'image d'origine selon l'intensité ;
  2. exposition (IL) ;  3. température et teinte ;  4. contraste et saturation.
Les médias d'origine ne sont jamais écrits : tout se passe dans le filtre de rendu.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .timeline.color import ColorGrade


def _q(path: Path | str) -> str:
    """Chemin sûr dans un filtre ffmpeg : séparateurs '/', ':' et quotes échappés (chemins Windows compris)."""
    p = str(path).replace("\\", "/")
    p = p.replace("'", r"\'").replace(":", r"\:").replace(",", r"\,").replace("[", r"\[").replace("]", r"\]")
    return p


def _temperature_filter(shift_k: float) -> str:
    # colortemperature de ffmpeg : valeur BASSE = image plus chaude ; 6500 K = neutre.
    return f"colortemperature=temperature={6500.0 - shift_k:.0f}:pl=0"


def _one(g: ColorGrade, resolve_lut: Callable[[str], Path], idx: int) -> list[str]:
    chain: list[str] = []
    if g.lut:
        lut = _q(resolve_lut(g.lut["path"]))
        if g.lut_strength >= 0.999:
            chain.append(f"lut3d=file={lut}:interp=tetrahedral")
        elif g.lut_strength > 0:
            s = g.lut_strength
            a, b, c = f"o{idx}", f"l{idx}", f"g{idx}"
            chain.append(f"split[{a}][{b}];[{b}]lut3d=file={lut}:interp=tetrahedral[{c}];"
                         f"[{a}][{c}]blend=all_expr=A*{1 - s:.4f}+B*{s:.4f}")
    if g.exposure_ev:
        chain.append(f"format=gbrpf32le,exposure=exposure={g.exposure_ev:.4f}")
    if g.temperature_k:
        chain.append(_temperature_filter(g.temperature_k))
    if g.tint:
        # teinte > 0 : vers le magenta = moins de vert
        chain.append(f"colorchannelmixer=gg={1.0 - 0.3 * g.tint / 100.0:.4f}")
    if g.contrast or g.saturation:
        chain.append(f"eq=contrast={1.0 + g.contrast / 100.0:.4f}:saturation={1.0 + g.saturation / 100.0:.4f}")
    return chain


def grade_filter(stack: list[dict] | None, resolve_lut: Callable[[str], Path]) -> str:
    """Chaîne ffmpeg pour une pile de réglages (dictionnaires) ; chaîne vide si rien d'actif."""
    parts: list[str] = []
    for i, d in enumerate(stack or []):
        g = ColorGrade.from_dict(d)
        if g.active:
            parts.extend(_one(g, resolve_lut, i))
    return ",".join(parts)
