"""Réglages de l'application résidente (JSON, écriture atomique). Aucun secret ici."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class AppSettings:
    start_with_windows: bool = False          # opt-in : jamais activé sans demande
    start_minimized: bool = True              # au démarrage de session : icône seule, sans fenêtre
    keep_running_on_close: bool = True        # fermer la fenêtre = masquer ; « Quitter » du menu arrête vraiment
    close_notice_shown: bool = False          # l'info « continue en arrière-plan » n'est affichée qu'une fois
    last_project: str = ""                    # dossier de rushes du dernier projet (pour rouvrir au démarrage réduit)


def load(path: Path) -> AppSettings:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppSettings()
    names = {f.name for f in fields(AppSettings)}
    return AppSettings(**{k: v for k, v in d.items() if k in names})


def save(path: Path, s: AppSettings) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
