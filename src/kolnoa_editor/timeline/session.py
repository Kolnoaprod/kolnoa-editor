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

from contextlib import contextmanager
from dataclasses import dataclass, field

from .model import Timeline, TimelineError
from .ops import Op, Transaction, execute, op_from_dict
from . import provenance as P


def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class _Step:
    label: str
    op: Op                                  # pile annuler : inverse ; pile rétablir : opération à rejouer
    origin: str = "manuel"
    group: str | None = None
    prov_before: dict = field(default_factory=dict)   # id -> état avant (None = absent) : restauré par annuler
    prov_after: dict = field(default_factory=dict)    # id -> état après : réappliqué par rétablir


class Session:
    def __init__(self, directory: Path, timeline: Timeline):
        self.dir = Path(directory)
        self.tl = timeline
        self._undo: list[_Step] = []
        self._redo: list[_Step] = []
        self._states: dict[str, str] = {}       # id de plan -> auto | manuel | valide (absent = manuel, protégé)
        self._group: tuple[str, str, P.Scope | None] | None = None   # (id de groupe, origine, portée) en cours
        self._group_seq = 0

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

    @property
    def provenance_path(self) -> Path:
        return self.dir / "provenance.json"

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
            self._do(op_from_dict(entry["op"]), entry.get("label", ""), entry.get("origin", "manuel"), entry.get("group"),
                     replay=True)
        elif kind == "undo":
            for _ in range(entry.get("n", 1)):
                self._undo_step()
        elif kind == "redo":
            for _ in range(entry.get("n", 1)):
                self._redo_step()
        else:
            raise TimelineError(f"Entrée de journal inconnue : {kind!r}")

    # --- actions en mémoire ------------------------------------------------
    def _do(self, op: Op, label: str, origin: str = "manuel", group: str | None = None, scope: "P.Scope | None" = None,
            confirmed_protected: bool = False, replay: bool = False) -> None:
        if origin not in P.ORIGINS:
            raise TimelineError(f"origine inconnue : {origin!r}")
        before = P.snapshot_all(self.tl)
        retired = list(self.tl.retired)
        inverse = execute(self.tl, op)
        chs = P.changes(before, P.snapshot_all(self.tl))
        if origin != "manuel" and not replay:
            problems = P.check_scope(chs, scope, self._states, confirmed_protected)
            if problems:
                execute(self.tl, inverse)                # rien n'a eu lieu : on revient à l'état exact d'avant
                self.tl.retired[:] = retired             # (les ids tentés n'ont jamais été journalisés)
                raise P.ScopeViolation(problems)
        prov_before, prov_after = {}, {}
        forced = op.state if isinstance(op, P.Mark) else None
        for c in chs:
            old = self._states.get(c.id)
            if forced is not None:
                new = forced
            elif c.kind == "removed":
                new = None
            elif c.kind == "shifted" and origin != "manuel":
                new = old                                # un décalage déclaré n'est pas une intervention
            else:
                new = P.MANUEL if origin == "manuel" else P.AUTO
            if new != old:
                prov_before[c.id], prov_after[c.id] = old, new
        if isinstance(op, P.Mark):
            for i in op.ids:
                old = self._states.get(i)
                if op.state != old:
                    prov_before[i], prov_after[i] = old, op.state
        self._apply_states(prov_after)
        self._undo.append(_Step(label or op.describe(), inverse, origin, group, prov_before, prov_after))
        self._redo.clear()

    def _apply_states(self, delta: dict) -> None:
        for i, st in delta.items():
            if st is None:
                self._states.pop(i, None)
            else:
                self._states[i] = st

    def _undo_step(self) -> str:
        if not self._undo:
            raise TimelineError("Rien à annuler")
        st = self._undo[-1]
        redo = execute(self.tl, st.op)
        self._undo.pop()
        self._apply_states(st.prov_before)
        self._redo.append(_Step(st.label, redo, st.origin, st.group, st.prov_before, st.prov_after))
        return st.label

    def _redo_step(self) -> str:
        if not self._redo:
            raise TimelineError("Rien à rétablir")
        st = self._redo[-1]
        inverse = execute(self.tl, st.op)
        self._redo.pop()
        self._apply_states(st.prov_after)
        self._undo.append(_Step(st.label, inverse, st.origin, st.group, st.prov_before, st.prov_after))
        return st.label

    # --- API publique ---------------------------------------------------------
    def do(self, op: Op, label: str = "", origin: str | None = None, scope: "P.Scope | None" = None,
           confirmed_protected: bool = False) -> list[str]:
        """Applique l'opération (atomique), l'inscrit au journal ; renvoie son diff lisible.
        `origin` : manuel (défaut) | auto | directive. Une transaction non manuelle exige une `scope` (ou un groupe qui en porte une)
        et ne peut ni sortir de cette zone ni modifier un plan protégé sans `confirmed_protected=True` (accord explicite)."""
        gid = gorigin = None
        if self._group is not None:
            gid, gorigin, gscope = self._group
            if origin is not None and origin != gorigin:
                raise TimelineError("origine différente de celle du groupe en cours")
            origin, scope = gorigin, scope or gscope
        origin = origin or "manuel"
        self._do(op, label, origin, gid, scope, confirmed_protected)
        try:
            entry = {"kind": "do", "label": label, "op": op.to_dict()}
            if origin != "manuel":
                entry["origin"] = origin
            if gid:
                entry["group"] = gid
            self._append(entry)
        except OSError:
            self._undo_step()                  # journal impossible : on ne garde pas une action non journalisée
            self._redo.clear()
            raise
        self._write_current()
        return op.descriptions() if isinstance(op, Transaction) else [op.describe()]

    @contextmanager
    def group(self, label: str, origin: str, scope: "P.Scope | None" = None):
        """Regroupe plusieurs `do` (p. ex. une génération complète) : `undo_group()` les annule d'un seul geste, chaque pas restant
        annulable seul. Non imbriqué."""
        if self._group is not None:
            raise TimelineError("groupe déjà ouvert")
        if origin == "manuel":
            raise TimelineError("un groupe est réservé aux origines auto/directive")
        self._group_seq += 1
        self._group = (f"g{self._group_seq:04d}", origin, scope)
        try:
            yield self._group[0]
        finally:
            self._group = None

    def undo_group(self) -> list[str]:
        """Annule d'un coup tous les pas consécutifs du dernier groupe (tout ou rien dans le journal)."""
        if not self._undo or self._undo[-1].group is None:
            raise TimelineError("le dernier pas n'appartient à aucun groupe")
        gid, labels = self._undo[-1].group, []
        while self._undo and self._undo[-1].group == gid:
            labels.append(self._undo_step())
        self._append({"kind": "undo", "n": len(labels)})
        self._write_current()
        return labels

    def validate_clips(self, ids: list[str], label: str = "valider") -> None:
        """Le réalisateur accepte ces plans : ils deviennent `valide` (protégés). Annulable."""
        self.do(P.Mark(list(ids), P.VALIDE), label)

    def state_of(self, clip_id: str) -> str:
        self.tl.clip(clip_id)
        return self._states.get(clip_id, P.DEFAULT_STATE)

    def provenance(self) -> dict[str, str]:
        return dict(self._states)

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
        return [st.label for st in self._undo]

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
        _write_json(self.provenance_path, dict(sorted(self._states.items())))   # dérivé du journal : reconstruit à l'ouverture
