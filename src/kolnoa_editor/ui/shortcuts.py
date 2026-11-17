"""Raccourcis clavier configurables de la timeline (fichier JSON de l'utilisateur, défauts raisonnables).

Une action = un raccourci ; deux actions ne peuvent pas partager la même combinaison (refus à la définition, pas de surprise).
Le fichier est optionnel : absent ou abîmé → défauts, jamais d'exception à l'ouverture de l'éditeur.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtGui import QKeySequence

DEFAULTS = {
    "razor": "S",                 # coupe à la tête de lecture : plans sélectionnés (ou toutes les pistes si rien n'est sélectionné)
    "razor_all": "Shift+S",       # coupe à la tête de lecture sur toutes les pistes déverrouillées
    "undo": "Ctrl+Z",
    "redo": "Ctrl+Shift+Z",
    "redo_alt": "Ctrl+Y",
}


def _norm(seq: str) -> str:
    return QKeySequence(seq).toString(QKeySequence.PortableText)


class Shortcuts:
    def __init__(self, mapping: dict[str, str] | None = None):
        self.map = {a: _norm(k) for a, k in DEFAULTS.items()}
        for a, k in (mapping or {}).items():
            if a in DEFAULTS:
                try:
                    self.set(a, k)
                except ValueError:
                    pass                      # une entrée en conflit dans le fichier est ignorée (défaut conservé)

    def set(self, action: str, key: str) -> None:
        if action not in DEFAULTS:
            raise ValueError(f"action inconnue : {action}")
        k = _norm(key)
        if not k:
            raise ValueError(f"raccourci invalide : {key!r}")
        for other, ok in self.map.items():
            if other != action and ok == k:
                raise ValueError(f"« {key} » est déjà utilisé par l'action « {other} »")
        self.map[action] = k

    def reset(self, action: str | None = None) -> None:
        for a in ([action] if action else list(DEFAULTS)):
            self.map[a] = _norm(DEFAULTS[a])

    def action_for(self, event) -> str | None:
        k = QKeySequence(event.keyCombination()).toString(QKeySequence.PortableText)
        return next((a for a, v in self.map.items() if v == k), None)

    # --- fichier --------------------------------------------------------------------------------------------
    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.map, indent=1, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Shortcuts":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(data if isinstance(data, dict) else None)
        except (OSError, ValueError):
            return cls()
