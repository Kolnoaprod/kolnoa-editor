"""Un projet = un dossier de mariage. Tout ce que génère Kolnoa va dans <mariage>/.kolnoa/."""

from __future__ import annotations

import json
from pathlib import Path


class Project:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"Dossier mariage introuvable : {self.root}")
        self.data = self.root / ".kolnoa"

    # --- emplacements -------------------------------------------------
    @property
    def library_path(self) -> Path:
        return self.data / "library.json"

    @property
    def proxies_dir(self) -> Path:
        return self.data / "proxies"

    @property
    def versions_dir(self) -> Path:
        return self.data / "versions"

    @property
    def comments_dir(self) -> Path:
        return self.data / "comments"

    @property
    def renders_dir(self) -> Path:
        return self.data / "renders"

    def ensure(self) -> None:
        for d in (self.data, self.proxies_dir, self.versions_dir, self.comments_dir, self.renders_dir):
            d.mkdir(parents=True, exist_ok=True)

    def proxy_path(self, rel: str) -> Path:
        return (self.proxies_dir / Path(rel)).with_suffix(".proxy.mp4")

    def version_path(self, version: int) -> Path:
        return self.versions_dir / f"v{version:03d}.json"

    def comments_path(self, version: int) -> Path:
        return self.comments_dir / f"v{version:03d}.json"

    def render_path(self, version: int, kind: str) -> Path:
        return self.renders_dir / f"v{version:03d}_{kind}.mp4"

    def resolve(self, rel: str) -> Path:
        """Chemin absolu d'un fichier source, interdit de sortir du dossier mariage."""
        path = (self.root / rel).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ValueError(f"Chemin hors du projet : {rel}")
        return path

    def next_version(self) -> int:
        existing = [
            int(p.stem[1:]) for p in self.versions_dir.glob("v*.json") if p.stem[1:].isdigit()
        ] if self.versions_dir.exists() else []
        return max(existing, default=0) + 1

    # --- json ---------------------------------------------------------
    @staticmethod
    def read_json(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def write_json(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
