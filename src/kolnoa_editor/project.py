"""Un projet = un dossier de mariage. Les données générées vont dans un dossier de données.

Par défaut c'est <mariage>/.kolnoa/. Attention : si le mariage est sur Google Drive, ce dossier est
synchronisé. Pour garder proxys, cache et rendus hors du Drive, définis KOLNOA_DATA_DIR (ex.
D:\\Kolnoa) ou passe --data-dir : chaque mariage y a alors son sous-dossier.
Les rushes originaux ne sont jamais écrits ni modifiés.
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import re
import time
from pathlib import Path


class Project:
    def __init__(self, root: Path | str, data_dir: Path | str | None = None):
        self.root = Path(root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"Dossier mariage introuvable : {self.root}")
        env = os.environ.get("KOLNOA_DATA_DIR")
        if data_dir:
            self.data = Path(data_dir)
        elif env:
            ident = hashlib.sha1(str(self.root.resolve()).lower().encode("utf-8")).hexdigest()[:8]
            slug = re.sub(r"[^\w\-]+", "_", self.root.name).strip("_")[:40] or "mariage"
            self.data = Path(env) / f"{slug}-{ident}"
        else:
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
    def timeline_dir(self) -> Path:
        return self.data / "timeline"

    @property
    def renders_dir(self) -> Path:
        return self.data / "renders"

    @property
    def cache_dir(self) -> Path:
        return self.data / "cache"

    @property
    def tmp_dir(self) -> Path:
        return self.data / "tmp"

    @property
    def logs_dir(self) -> Path:
        return self.data / "logs"

    def ensure(self) -> None:
        for d in (self.data, self.proxies_dir, self.versions_dir, self.comments_dir,
                  self.renders_dir, self.cache_dir, self.tmp_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def proxy_path(self, rel: str) -> Path:
        return (self.proxies_dir / Path(rel)).with_suffix(".proxy.mp4")

    def version_path(self, version: int) -> Path:
        return self.versions_dir / f"v{version:03d}.json"

    def comments_path(self, version: int) -> Path:
        return self.comments_dir / f"v{version:03d}.json"

    def render_path(self, version: int, kind: str) -> Path:
        return self.renders_dir / f"v{version:03d}_{kind}.mp4"

    def is_in_data_dir(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.data.resolve())
            return True
        except ValueError:
            return False

    def resolve(self, rel: str) -> Path:
        """Chemin absolu d'un fichier source, interdit de sortir du dossier mariage."""
        path = (self.root / rel).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ValueError(f"Chemin hors du projet : {rel}")
        return path

    def latest_version(self) -> int:
        if not self.versions_dir.exists():
            return 0
        nums = [int(p.stem[1:]) for p in self.versions_dir.glob("v*.json") if p.stem[1:].isdigit()]
        return max(nums, default=0)

    def next_version(self) -> int:
        return self.latest_version() + 1

    # --- json ---------------------------------------------------------
    @staticmethod
    def read_json(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def write_json(path: Path, data) -> None:
        """Écriture atomique : un crash ne laisse jamais un fichier tronqué."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def quarantine(path: Path) -> Path:
        """Met de côté un fichier illisible au lieu de l'écraser."""
        target = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        os.replace(path, target)
        return target

    @staticmethod
    def quarantine_dir(path: Path) -> Path:
        """Met un dossier de côté (renommé, jamais supprimé)."""
        target = path.with_name(f"{path.name}.old-{int(time.time())}")
        os.replace(path, target)
        return target

    # --- journaux -----------------------------------------------------
    def configure_logging(self, level: int = logging.INFO) -> Path:
        """Journal tournant dans <données>/logs/kolnoa.log (détails ffmpeg inclus)."""
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        logfile = self.logs_dir / "kolnoa.log"
        logger = logging.getLogger("kolnoa")
        logger.setLevel(logging.DEBUG)
        for h in list(logger.handlers):
            if getattr(h, "_kolnoa_file", False):
                logger.removeHandler(h)
                h.close()
        handler = logging.handlers.RotatingFileHandler(
            logfile, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler._kolnoa_file = True  # type: ignore[attr-defined]
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        return logfile
