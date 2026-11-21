"""Catalogue des projets (écran d'accueil façon DaVinci / Premiere) : projets créés dans le dossier de travail + historique.
Ne touche jamais aux rushes ; « retirer de la liste » n'efface rien sur le disque."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .media_manifest import MediaManifest

HISTORY_MAX = 40


def _slug(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._") or "projet"


class ProjectCatalog:
    def __init__(self, base: Path):
        self.base = Path(base)
        self.history_path = self.base / "historique.json"

    # --- historique ----------------------------------------------------------------------------------------
    def _history(self) -> list[dict]:
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
            return [h for h in raw.get("recent", []) if isinstance(h, dict) and h.get("path")]
        except (OSError, ValueError):
            return []

    def _save_history(self, items: list[dict]) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        tmp = self.history_path.with_suffix(".part")
        tmp.write_text(json.dumps({"version": 1, "recent": items[:HISTORY_MAX]}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.history_path)

    def touch(self, path: Path | str) -> None:
        key = str(Path(path))
        items = [h for h in self._history() if h["path"] != key]
        items.insert(0, {"path": key, "opened_at": datetime.now().isoformat(timespec="seconds")})
        try:
            self._save_history(items)
        except OSError:
            pass                                             # l'historique est un confort : jamais bloquant

    def forget(self, path: Path | str) -> None:
        key = str(Path(path))
        try:
            self._save_history([h for h in self._history() if h["path"] != key])
        except OSError:
            pass

    # --- création ------------------------------------------------------------------------------------------
    def create(self, name: str) -> Path:
        """Nouveau projet vide : dossier <base>/<nom>/ avec <nom>.klnprj et donnees/. Retourne le chemin du .klnprj."""
        name = name.strip()
        if not name:
            raise ValueError("Donnez un nom au projet.")
        folder = self.base / _slug(name)
        if folder.exists() or (self.base.is_dir() and any(p.name.casefold() == folder.name.casefold() for p in self.base.iterdir())):
            raise ValueError(f"Un projet « {name} » existe déjà.")
        (folder / "donnees").mkdir(parents=True)
        manifest = MediaManifest(folder / f"{_slug(name)}.klnprj")
        manifest.root = str(folder)
        manifest.data_dir = str(folder / "donnees")
        manifest.save()
        self.touch(manifest.path)
        return manifest.path

    # --- liste ---------------------------------------------------------------------------------------------
    def _describe(self, path: Path, opened: str | None) -> dict | None:
        try:
            if path.is_file() and path.suffix.lower() == ".klnprj":
                m = MediaManifest(path)
                vids = sum(1 for r in m.media if r.kind == "video")
                when = opened or datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
                return {"name": path.stem, "path": path, "kind": "projet", "rushes": vids, "sequences": len(m.sequences) + 1, "when": when}
            if path.is_dir():
                when = opened or datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
                return {"name": path.name, "path": path, "kind": "dossier", "rushes": None, "sequences": None, "when": when}
        except (OSError, ValueError, KeyError):
            return None
        return None

    def entries(self) -> list[dict]:
        seen: dict[str, dict] = {}
        for h in self._history():
            p = Path(h["path"])
            d = self._describe(p, h.get("opened_at"))
            if d:
                seen[str(p)] = d
        if self.base.is_dir():                                  # projets créés ici mais pas (encore) dans l'historique
            for p in sorted(self.base.glob("*/*.klnprj")):
                if str(p) not in seen:
                    d = self._describe(p, None)
                    if d:
                        seen[str(p)] = d
        return sorted(seen.values(), key=lambda e: e["when"], reverse=True)
