"""Session d'édition persistante : annuler/rétablir qui survivent à un redémarrage.

Dossier de session :
  base.json      état de départ (jamais modifié après création)
  history.jsonl  journal : une ligne par action (do / undo / redo), ajoutée AVANT d'être acquittée
  current.json   état courant (dérivé, réécrit atomiquement ; sert à la lecture et aux outils externes)

Ouvrir une session = relire `base.json` puis rejouer le journal : l'état et les piles
annuler/rétablir sont reconstruits à l'identique. Une dernière ligne tronquée par un crash
est ignorée (l'action correspondante n'avait pas été acquittée).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .model import Timeline, TimelineError
from .ops import Op, Transaction, execute, op_from_dict


def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class Session:
    def __init__(self, directory: Path, timeline: Timeline):
        self.dir = Path(directory)
        self.tl = timeline
        self._undo: list[tuple[str, Op]] = []   # (libellé, inverse)
        self._redo: list[tuple[str, Op]] = []   # (libellé, opération à rejouer)

    # --- chemins ---------------------------------------------------------
    @property
    def base_path(self) -> Path:
        return self.dir / "base.json"

    @property
    def journal_path(self) -> Path:
        return self.dir / "history.jsonl"

    @property
    def current_path(self) -> Path:
        return self.dir / "current.json"

    # --- création / ouverture -----------------------------------------------
    @classmethod
    def create(cls, directory: Path, timeline: Timeline) -> "Session":
        directory = Path(directory)
        if (directory / "base.json").exists():
            raise TimelineError(f"Une session existe déjà : {directory}")
        timeline.validate()
        directory.mkdir(parents=True, exist_ok=True)
        s = cls(directory, timeline)
        _write_json(s.base_path, timeline.to_dict())
        s.journal_path.write_text("", encoding="utf-8")
        s._write_current()
        return s

    @classmethod
    def open(cls, directory: Path) -> "Session":
        directory = Path(directory)
        try:
            base = Timeline.from_dict(json.loads((directory / "base.json").read_text(encoding="utf-8")))
        except FileNotFoundError as exc:
            raise TimelineError(f"Aucune session dans {directory}") from exc
        s = cls(directory, base)
        lines = s.journal_path.read_text(encoding="utf-8").splitlines() if s.journal_path.exists() else []
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                if i == len(lines) - 1:        # ligne finale tronquée par un crash : ignorée
                    break
                raise TimelineError(f"Journal corrompu à la ligne {i + 1}") from None
            s._replay(entry)
        s._write_current()
        return s

    def _replay(self, entry: dict) -> None:
        kind = entry["kind"]
        if kind == "do":
            self._do(op_from_dict(entry["op"]), entry.get("label", ""))
        elif kind == "undo":
            self._undo_step()
        elif kind == "redo":
            self._redo_step()
        else:
            raise TimelineError(f"Entrée de journal inconnue : {kind!r}")

    # --- actions en mémoire ------------------------------------------------
    def _do(self, op: Op, label: str) -> None:
        inverse = execute(self.tl, op)
        self._undo.append((label or op.describe(), inverse))
        self._redo.clear()

    def _undo_step(self) -> str:
        if not self._undo:
            raise TimelineError("Rien à annuler")
        label, inverse = self._undo[-1]
        redo = execute(self.tl, inverse)
        self._undo.pop()
        self._redo.append((label, redo))
        return label

    def _redo_step(self) -> str:
        if not self._redo:
            raise TimelineError("Rien à rétablir")
        label, op = self._redo[-1]
        inverse = execute(self.tl, op)
        self._redo.pop()
        self._undo.append((label, inverse))
        return label

    # --- API publique ---------------------------------------------------------
    def do(self, op: Op, label: str = "") -> list[str]:
        """Applique l'opération (atomique), l'inscrit au journal ; renvoie son diff lisible."""
        self._do(op, label)
        try:
            self._append({"kind": "do", "label": label, "op": op.to_dict()})
        except OSError:
            self._undo_step()                  # journal impossible : on ne garde pas une action non journalisée
            self._redo.clear()
            raise
        self._write_current()
        return op.descriptions() if isinstance(op, Transaction) else [op.describe()]

    def undo(self) -> str:
        label = self._undo_step()
        self._append({"kind": "undo"})
        self._write_current()
        return label

    def redo(self) -> str:
        label = self._redo_step()
        self._append({"kind": "redo"})
        self._write_current()
        return label

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def history(self) -> list[str]:
        return [label for label, _ in self._undo]

    def snapshot(self, name: str) -> Path:
        """Instantané nommé de l'état courant (jamais écrasé)."""
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name) or "snapshot"
        path = self.dir / "snapshots" / f"{safe}.json"
        if path.exists():
            path = path.with_name(f"{safe}-{int(time.time())}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, self.tl.to_dict())
        return path

    # --- fichiers ---------------------------------------------------------------
    def _append(self, entry: dict) -> None:
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _write_current(self) -> None:
        _write_json(self.current_path, self.tl.to_dict())
