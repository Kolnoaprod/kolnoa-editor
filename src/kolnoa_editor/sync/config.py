"""Réglages de la synchronisation. TOUS les seuils sont des valeurs initiales à calibrer sur de vrais
rushes (voir docs/SYNC.md) : aucun n'est une vérité universelle. Ils se surchargent par fichier JSON
(`kolnoa multicam-sync --config sync.json`) sans toucher au code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, asdict
from pathlib import Path


@dataclass
class SyncConfig:
    # --- extraction audio -------------------------------------------------
    rate: int = 8000                    # Hz, pour l'affinage (bande utile : parole, applaudissements, musique)
    band_hz: tuple[float, float] = (150.0, 3500.0)   # bande utilisée par la corrélation de phase
    env_rate: int = 100                 # Hz, enveloppe d'énergie pour la recherche grossière
    coarse_ambiguity_ratio: float = 0.85   # un 2e alignement ≥ ratio × le 1er (enveloppes) rend le résultat « incertain »
    coarse_ambiguity_exclusion_s: float = 3.0   # un pic concurrent doit se trouver à plus de ce délai du pic retenu
    empty_audio_peak: float = 1e-5       # crête (échelle 0-1) sous laquelle une piste est « vide » (silence numérique)
    # --- fenêtres ---------------------------------------------------------
    window_s: float = 20.0              # durée d'une fenêtre d'affinage
    max_windows: int = 12               # nombre maximal de fenêtres réparties sur le clip
    wide_search_s: float = 60.0         # recherche grossière locale autour de l'attendu (détecte les sauts)
    refine_s: float = 0.4               # demi-plage de l'affinage par corrélation de phase
    silence_ratio: float = 0.1          # fenêtre ignorée si son énergie < ratio × médiane des fenêtres
    # --- décisions de fiabilité -------------------------------------------
    min_psr: float = 8.0                # pic/bruit minimal d'une fenêtre pour la compter
    good_psr: float = 20.0              # pic/bruit à partir duquel la fenêtre est « très sûre »
    min_env_psr: float = 5.0            # pic/bruit minimal de la recherche grossière globale
    coverage_good: float = 0.6          # part de fenêtres valides jugée suffisante
    min_windows: int = 3                # fenêtres valides minimales (si le clip en permet autant)
    inlier_tol_s: float = 0.05          # écart toléré à la droite offset/dérive. Mesuré sur 2 micros sans fil DJI (MAIRIE) : ±25 ms de gigue entre fenêtres
    jump_tol_s: float = 0.25            # saut d'offset entre fenêtres voisines = discontinuité
    max_unmatched_ratio: float = 0.25   # au-delà de cette part de fenêtres sans correspondance cohérente, jamais « ok »
    ok_confidence: float = 0.70         # ≥ : « ok »
    uncertain_confidence: float = 0.35  # ≥ : « incertain » ; en dessous : « impossible »
    drift_compensation_ppm: float = 100.0   # dérive mesurée à partir de laquelle on recompense chaque fenêtre
    max_drift_ppm: float = 5000.0       # au-delà, la dérive mesurée est jugée suspecte
    # --- métadonnées (indice seulement) -------------------------------------
    clock_tolerance_s: float = 300.0    # écart d'horloge supposé entre appareils non synchronisés
    # --- visuel (expérimental) --------------------------------------------
    visual_fps: float = 10.0            # cadence d'analyse des séries de luminosité
    visual_max_confidence: float = 0.6  # plafond : une synchro visuelle n'est jamais « ok »
    visual_min_events: int = 2          # événements marquants minimum de chaque côté
    visual_event_min_delta: float = 8.0   # variation minimale de luminosité (échelle 0-255) pour compter comme événement
    visual_event_min_gap_s: float = 1.0   # événements plus proches que ça = un seul (un flash donne plusieurs images consécutives)
    visual_event_z: float = 3.0         # seuil d'événement (écarts-types de la variation de luminosité)
    visual_min_psr: float = 5.0         # netteté minimale du pic de corrélation d'événements
    visual_good_psr: float = 12.0
    visual_match_s: float = 0.3         # deux événements à moins de ce délai sont « les mêmes »
    visual_max_coincidence: float = 0.01   # score heuristique : au-delà, indice « faible » (jamais une proposition)
    visual_weak_coincidence: float = 0.2   # score heuristique : au-delà, aucune correspondance démontrée (impossible)
    visual_top_k: int = 3               # candidats proposés
    visual_ambiguity_ratio: float = 0.85  # 2e candidat ≥ ratio × 1er : proposition jugée ambiguë

    def __post_init__(self) -> None:
        self.band_hz = tuple(self.band_hz)
        self.validate()

    def validate(self) -> None:
        """Refuse les réglages absurdes avec un message clair (un seuil faux donne des synchros fausses, en silence)."""
        errs: list[str] = []

        def pos(name: str, strict: bool = True, integer: bool = False) -> None:
            v = getattr(self, name)
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and (v > 0 if strict else v >= 0)
            if integer:
                ok = ok and float(v).is_integer()
            if not ok:
                errs.append(f"{name} doit être un {'entier ' if integer else 'nombre '}{'> 0' if strict else '≥ 0'} (reçu {v!r})")

        def unit(name: str) -> None:
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0):
                errs.append(f"{name} doit être compris entre 0 et 1 (reçu {v!r})")

        for n in ("rate", "env_rate", "max_windows", "min_windows", "visual_min_events", "visual_top_k"):
            pos(n, integer=True)
        for n in ("window_s", "wide_search_s", "refine_s", "min_psr", "good_psr", "min_env_psr", "inlier_tol_s", "jump_tol_s",
                  "max_drift_ppm", "clock_tolerance_s", "visual_fps", "visual_event_min_gap_s", "visual_event_z",
                  "visual_min_psr", "visual_good_psr", "visual_match_s"):
            pos(n)
        pos("drift_compensation_ppm", strict=False)
        pos("visual_event_min_delta", strict=False)
        pos("silence_ratio", strict=False)
        pos("coarse_ambiguity_exclusion_s")
        pos("empty_audio_peak", strict=False)
        for n in ("coarse_ambiguity_ratio", "max_unmatched_ratio", "coverage_good", "ok_confidence", "uncertain_confidence", "visual_max_confidence", "visual_max_coincidence",
                  "visual_weak_coincidence", "visual_ambiguity_ratio"):
            unit(n)
        b = self.band_hz
        if len(b) != 2 or not all(isinstance(x, (int, float)) for x in b) or not (0 < b[0] < b[1]):
            errs.append(f"band_hz doit être (bas, haut) avec 0 < bas < haut (reçu {b!r})")
        elif isinstance(self.rate, (int, float)) and self.rate > 0 and b[1] >= self.rate / 2:
            errs.append(f"band_hz : la fréquence haute ({b[1]} Hz) doit rester sous la moitié de la cadence d'analyse ({self.rate / 2} Hz)")
        try:
            if self.good_psr <= self.min_psr:
                errs.append(f"good_psr ({self.good_psr}) doit dépasser min_psr ({self.min_psr})")
            if self.visual_good_psr <= self.visual_min_psr:
                errs.append(f"visual_good_psr ({self.visual_good_psr}) doit dépasser visual_min_psr ({self.visual_min_psr})")
            if self.uncertain_confidence >= self.ok_confidence:
                errs.append(f"uncertain_confidence ({self.uncertain_confidence}) doit rester sous ok_confidence ({self.ok_confidence})")
            if self.visual_max_coincidence >= self.visual_weak_coincidence:
                errs.append("visual_max_coincidence doit rester sous visual_weak_coincidence")
            if self.jump_tol_s <= self.inlier_tol_s:
                errs.append(f"jump_tol_s ({self.jump_tol_s}) doit dépasser inlier_tol_s ({self.inlier_tol_s})")
            if self.refine_s > self.window_s / 2:
                errs.append("refine_s ne peut pas dépasser la moitié de window_s")
        except TypeError:       # type déjà signalé plus haut
            pass
        if errs:
            raise ValueError("réglages de synchro invalides :\n  - " + "\n  - ".join(errs))

    @classmethod
    def from_dict(cls, d: dict) -> "SyncConfig":
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"réglages de synchro inconnus : {sorted(unknown)}")
        d = dict(d)
        if "band_hz" in d:
            d["band_hz"] = tuple(d["band_hz"])
        return cls(**d)

    @classmethod
    def load(cls, path: Path | str | None) -> "SyncConfig":
        if path is None:
            return cls()
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["band_hz"] = list(self.band_hz)
        return d
