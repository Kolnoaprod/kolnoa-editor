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
    inlier_tol_s: float = 0.03          # écart toléré à la droite offset/dérive (≈ 1 image à 25 i/s)
    jump_tol_s: float = 0.25            # saut d'offset entre fenêtres voisines = discontinuité
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
    visual_event_z: float = 3.0         # seuil d'événement (écarts-types de la variation de luminosité)
    visual_min_psr: float = 5.0         # netteté minimale du pic de corrélation d'événements
    visual_good_psr: float = 12.0
    visual_match_s: float = 0.3         # deux événements à moins de ce délai sont « les mêmes »
    visual_max_chance_p: float = 0.01   # au-delà, la concordance d'événements s'explique par le hasard : impossible
    visual_weak_chance_p: float = 0.2   # entre les deux seuils : « indice faible » (candidat à vérifier à la main)
    visual_top_k: int = 3               # candidats proposés
    visual_ambiguity_ratio: float = 0.85  # 2e candidat ≥ ratio × 1er : proposition jugée ambiguë

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
