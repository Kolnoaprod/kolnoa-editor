"""Moteur résident (sans Qt) : réglages, tâches, instance unique. L'interface Qt (fenêtre, icône) et — plus tard — l'API Companion n'en sont que des vues.

Rien ici ne dépend de la fenêtre : la masquer ou la fermer ne touche ni aux tâches ni aux projets ouverts."""

from __future__ import annotations

from pathlib import Path

from . import settings as S
from .autostart import Autostart
from .instance import InstanceLock, engine_dir
from .jobs import JobRegistry


class EngineAlreadyRunning(RuntimeError):
    pass


class ResidentEngine:
    def __init__(self, directory: Path | None = None, autostart: Autostart | None = None):
        self.dir = Path(directory) if directory else engine_dir()
        self.lock = InstanceLock(self.dir)
        self.jobs = JobRegistry(self.dir / "jobs.jsonl")
        self.autostart = autostart or Autostart()
        self.settings_path = self.dir / "settings.json"
        self.settings = S.load(self.settings_path)
        self.controllers: dict[str, object] = {}              # projets ouverts (clé = dossier de rushes) — point d'accroche de l'API Companion
        self.api_endpoint: str | None = None                  # renseigné quand le serveur Companion existera

    def start(self) -> None:
        if not self.lock.acquire():
            raise EngineAlreadyRunning("Kolnoa Editor est déjà lancé pour cette session")
        self.settings = S.load(self.settings_path)

    def update_settings(self, **kw) -> None:
        for k, v in kw.items():
            if not hasattr(self.settings, k):
                raise KeyError(k)
            setattr(self.settings, k, v)
        S.save(self.settings_path, self.settings)
        if "start_with_windows" in kw or "start_minimized" in kw:
            self.autostart.apply(self.settings.start_with_windows, self.settings.start_minimized)

    def attach_project(self, ctl) -> None:
        self.controllers[str(ctl.project.root)] = ctl
        self.update_settings(last_project=str(ctl.project.root))

    def status(self) -> dict:
        """Photographie lisible sans Qt (future route `GET /machine`)."""
        return {"running": self.lock.held, "projects": list(self.controllers), "api": self.api_endpoint,
                "jobs": [j.public() for j in self.jobs.jobs()], "settings": vars(self.settings).copy()}

    def shutdown(self) -> None:
        self.jobs.cancel_all()
        self.jobs.wait_idle(10)
        self.lock.release()
