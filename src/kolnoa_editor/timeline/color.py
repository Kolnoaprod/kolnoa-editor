"""Étalonnage non destructif : un réglage de couleur est une donnée de la timeline, jamais un fichier modifié.

Un réglage (`ColorGrade`) s'attache à une portée :
  - `global`                : tout le film ;
  - `angle:<groupe>/<angle>`: un angle d'un groupe multicam (CAM 2 partout) ;
  - `clip:<id>`             : un seul plan.
Pour un plan, les réglages applicables s'EMPILENT dans cet ordre : global, angle, plan.

Ordre fixe à l'intérieur d'un réglage : LUT (intensité 0..1) puis réglages manuels
(exposition, température, teinte, contraste, saturation).

Prudence assumée : `input_profile="auto"` signifie « non confirmé » ; on ne suppose jamais un profil
LOG. Ce module ne convertit pas d'espace colorimétrique (pas de maths S-Log3 maison) : la conversion
passe par une LUT technique dont les espaces d'entrée/sortie sont déclarés. Une LUT dont les droits
d'utilisation ne sont pas confirmés est signalée et n'est jamais copiée dans le projet (on garde son chemin).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

INPUT_PROFILES = {"auto", "rec709", "s-cinetone", "s-log3", "hlg", "autre"}
LOG_PROFILES = {"s-log3"}
LUT_RIGHTS = {"unknown", "confirmed"}
RANGES = {  # (min, max)
    "lut_strength": (0.0, 1.0), "exposure_ev": (-4.0, 4.0), "temperature_k": (-3000.0, 3000.0),
    "tint": (-100.0, 100.0), "contrast": (-100.0, 100.0), "saturation": (-100.0, 100.0),
}
SCOPE_GLOBAL = "global"


class ColorError(ValueError):
    """Réglage de couleur invalide."""


@dataclass
class ColorGrade:
    enabled: bool = True
    input_profile: str = "auto"
    lut: dict[str, Any] | None = None        # {path, input_space, output_space, rights}
    lut_strength: float = 1.0                # 0..1
    exposure_ev: float = 0.0                 # IL
    temperature_k: float = 0.0               # > 0 : plus chaud, < 0 : plus froid
    tint: float = 0.0                        # > 0 : vers le magenta, < 0 : vers le vert
    contrast: float = 0.0
    saturation: float = 0.0
    note: str = ""

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        if self.input_profile not in INPUT_PROFILES:
            raise ColorError(f"profil d'entrée inconnu : {self.input_profile!r} (valeurs : {sorted(INPUT_PROFILES)})")
        for name, (lo, hi) in RANGES.items():
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
                raise ColorError(f"{name} doit être un nombre entre {lo} et {hi} (reçu {v!r})")
        if self.lut is not None:
            if not isinstance(self.lut, dict) or not str(self.lut.get("path", "")).strip():
                raise ColorError("lut : un chemin est obligatoire")
            lut = self.lut
            unknown = set(lut) - {"path", "input_space", "output_space", "rights"}
            if unknown:
                raise ColorError(f"lut : champs inconnus {sorted(unknown)}")
            if lut.get("rights", "unknown") not in LUT_RIGHTS:
                raise ColorError(f"lut.rights doit être 'unknown' ou 'confirmed' (reçu {lut.get('rights')!r})")
            if not str(lut["path"]).lower().endswith(".cube"):
                raise ColorError("lut : seuls les fichiers .cube sont pris en charge")

    @property
    def neutral(self) -> bool:
        return (self.lut is None and not any((self.exposure_ev, self.temperature_k, self.tint,
                                              self.contrast, self.saturation)))

    @property
    def active(self) -> bool:
        return self.enabled and not self.neutral

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ColorGrade":
        if not isinstance(d, dict):
            raise ColorError("réglage de couleur mal formé")
        try:
            return cls(**d)
        except TypeError as exc:
            raise ColorError(f"réglage de couleur mal formé : {exc}") from exc

    def describe(self) -> str:
        parts = []
        if self.lut:
            parts.append(f"LUT {self.lut['path']} ({self.lut_strength:.0%})")
        for label, v, unit in (("expo", self.exposure_ev, " IL"), ("temp", self.temperature_k, " K"),
                               ("teinte", self.tint, ""), ("contraste", self.contrast, ""),
                               ("saturation", self.saturation, "")):
            if v:
                parts.append(f"{label} {v:+g}{unit}")
        if self.input_profile != "auto":
            parts.append(f"profil {self.input_profile}")
        return ", ".join(parts) or "neutre"


# --- portées -------------------------------------------------------------
def clip_scope(clip_id: str) -> str:
    return f"clip:{clip_id}"


def angle_scope(group_id: str, angle_id: str) -> str:
    return f"angle:{group_id}/{angle_id}"


def check_scope(tl, scope: str, strict: bool = True) -> None:
    """Lève ColorError si la portée est mal formée ; en mode strict, aussi si elle n'existe pas dans la timeline.
    (Un réglage dont le plan a été supprimé reste dormant : l'annulation doit pouvoir le retrouver.)"""
    if scope == SCOPE_GLOBAL:
        return
    kind, _, ref = scope.partition(":")
    try:
        if kind == "clip" and ref:
            if strict:
                tl.clip(ref)
            return
        if kind == "angle" and "/" in ref:
            gid, _, aid = ref.partition("/")
            if strict:
                tl.group(gid).angle(aid)
            return
    except ValueError as exc:  # TimelineError hérite de ValueError
        raise ColorError(f"portée {scope!r} : {exc}") from exc
    raise ColorError(f"portée invalide : {scope!r} (global, clip:<id> ou angle:<groupe>/<angle>)")


def validate_grades(tl) -> None:
    for scope, d in tl.grades.items():
        check_scope(tl, scope, strict=False)
        ColorGrade.from_dict(d)


def stack_for_clip(tl, clip) -> list[ColorGrade]:
    """Réglages actifs applicables à un plan, dans l'ordre d'application : global, angle, plan."""
    scopes = [SCOPE_GLOBAL]
    if clip.group and clip.angle:
        scopes.append(angle_scope(clip.group, clip.angle))
    scopes.append(clip_scope(clip.id))
    out = []
    for s in scopes:
        key = s
        if s == clip_scope(clip.id):
            key = _inherited_clip_scope(tl, clip)
        if key in tl.grades:
            g = ColorGrade.from_dict(tl.grades[key])
            if g.active:
                out.append(g)
    return out


def _inherited_clip_scope(tl, clip) -> str:
    """Portée de plan applicable : celle du plan, sinon celle de l'ancêtre le plus proche (scission).
    Ainsi couper un plan étalonné ne fait pas perdre sa couleur à la seconde moitié."""
    by_id = {c.id: c for c in tl.clips}
    cur, seen = clip, set()
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        if clip_scope(cur.id) in tl.grades:
            return clip_scope(cur.id)
        cur = by_id.get(cur.parent) if cur.parent else None
    return clip_scope(clip.id)


def grade_warnings(tl, lut_exists=None) -> list[str]:
    """Avertissements lisibles (jamais bloquants) sur les réglages de la timeline.
    `lut_exists(path) -> bool` permet de vérifier la présence des fichiers LUT."""
    out: list[str] = []
    for scope, d in sorted(tl.grades.items()):
        try:
            check_scope(tl, scope)
        except ColorError:
            out.append(f"{scope}: réglage orphelin (plan ou angle absent), sans effet")
        g = ColorGrade.from_dict(d)
        if not g.enabled:
            continue
        if g.lut:
            if g.lut.get("rights", "unknown") != "confirmed":
                out.append(f"{scope}: droits d'utilisation de la LUT non confirmés ({g.lut['path']})")
            if lut_exists is not None and not lut_exists(g.lut["path"]):
                out.append(f"{scope}: fichier LUT introuvable ({g.lut['path']})")
            declared = g.lut.get("input_space")
            if declared and g.input_profile not in ("auto", declared):
                out.append(f"{scope}: la LUT attend {declared!r} mais le profil déclaré est {g.input_profile!r}")
        elif g.input_profile in LOG_PROFILES:
            out.append(f"{scope}: profil {g.input_profile} sans LUT de conversion : l'image restera en LOG")
    return out
