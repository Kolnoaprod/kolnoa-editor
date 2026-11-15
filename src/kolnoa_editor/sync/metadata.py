"""Indice de synchro par métadonnées (horodatage des fichiers). INDICE SEULEMENT : les horloges des appareils ne
sont pas réglées ensemble (écart de quelques secondes à plusieurs minutes), le résultat ne sert qu'à restreindre
la recherche grossière ; il n'est jamais appliqué comme synchro."""

from __future__ import annotations

from datetime import datetime

from .config import SyncConfig


def metadata_hint(master_start_iso: str | None, angle_start_iso: str | None, cfg: SyncConfig | None = None,
                  master_offset: float = 0.0) -> tuple[float, float] | None:
    """(décalage attendu, tolérance) de l'angle sur l'axe du groupe, ou None si une date manque."""
    cfg = cfg or SyncConfig()
    if not master_start_iso or not angle_start_iso:
        return None
    try:
        delta = (datetime.fromisoformat(angle_start_iso) - datetime.fromisoformat(master_start_iso)).total_seconds()
    except ValueError:
        return None
    return master_offset + delta, cfg.clock_tolerance_s
