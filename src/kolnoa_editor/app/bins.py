"""Dossiers virtuels de la médiathèque : pure organisation (bins.json dans les données du projet).
Aucun rush n'est déplacé, copié ni modifié sur le disque ; un rush peut être rangé dans un seul dossier virtuel à la fois."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4


class BinFolders:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.folders: list[dict] = []                       # {"id", "name", "media": [chemins relatifs]}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.folders = [f for f in data.get("folders", []) if isinstance(f, dict) and f.get("id") and f.get("name")]
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{uuid4().hex}.part")
        try:
            tmp.write_text(json.dumps({"version": 1, "folders": self.folders}, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def _find(self, fid: str) -> dict:
        for f in self.folders:
            if f["id"] == fid:
                return f
        raise KeyError(fid)

    def create(self, name: str) -> str:
        name = name.strip()
        if not name or any(f["name"].casefold() == name.casefold() for f in self.folders):
            raise ValueError("Donnez un nom de dossier unique.")
        fid = uuid4().hex[:8]
        self.folders.append({"id": fid, "name": name, "media": []})
        self.save()
        return fid

    def rename(self, fid: str, name: str) -> None:
        name = name.strip()
        if not name or any(f["name"].casefold() == name.casefold() and f["id"] != fid for f in self.folders):
            raise ValueError("Donnez un nom de dossier unique.")
        self._find(fid)["name"] = name
        self.save()

    def delete(self, fid: str) -> None:
        """Supprime le dossier virtuel ; ses rushes redeviennent « non rangés » (rien n'est effacé du disque)."""
        self.folders.remove(self._find(fid))
        self.save()

    def assign(self, media: list[str], fid: str | None) -> None:
        moving = set(media)
        for f in self.folders:
            f["media"] = [m for m in f["media"] if m not in moving]
        if fid is not None:
            self._find(fid)["media"].extend(sorted(moving))
        self.save()

    def folder_of(self, media: str) -> str | None:
        for f in self.folders:
            if media in f["media"]:
                return f["id"]
        return None

    def names(self) -> dict[str, str]:
        return {f["id"]: f["name"] for f in self.folders}
