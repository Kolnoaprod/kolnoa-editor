"""Tâches du moteur (V0 du gestionnaire de tâches) : liste en mémoire, thread-safe, observable, annulable ; historique en JSON Lines.

Sans Qt : la fenêtre, l'icône de notification et (plus tard) l'API Companion lisent la même liste."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Callable

from ..ffmpeg import CancelToken, Cancelled

ACTIVE = ("queued", "running")


@dataclass
class Job:
    id: str
    kind: str                       # "export" | "analyse" | "montage" | …
    label: str
    state: str = "queued"           # queued | running | done | failed | cancelled
    progress: float = 0.0           # 0..1
    message: str = ""
    project: str = ""
    started: float = 0.0
    ended: float = 0.0
    cancel: CancelToken = field(default_factory=CancelToken, repr=False, compare=False)

    def public(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "cancel"}


class JobRegistry:
    def __init__(self, log_path: Path | None = None):
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._subs: list[Callable[[Job], None]] = []
        self._log = Path(log_path) if log_path else None

    def subscribe(self, fn: Callable[[Job], None]) -> None:
        with self._lock:
            self._subs.append(fn)

    def _emit(self, job: Job) -> None:
        for fn in list(self._subs):
            try:
                fn(job)
            except Exception:                                # noqa: BLE001 — un observateur défaillant ne bloque jamais une tâche
                pass

    def create(self, kind: str, label: str, project: str = "") -> Job:
        job = Job(uuid.uuid4().hex[:12], kind, label, project=project)
        with self._lock:
            self._jobs[job.id] = job
        self._emit(job)
        return job

    def update(self, job: Job, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(job, k, v)
            if job.state not in ACTIVE and not job.ended:
                job.ended = time.time()
        if job.state not in ACTIVE:
            self._append_log(job)
        self._emit(job)

    def _append_log(self, job: Job) -> None:
        if not self._log:
            return
        try:
            self._log.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log, "a", encoding="utf-8") as f:
                f.write(json.dumps(job.public(), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def jobs(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.started or float("inf"))

    def active(self) -> list[Job]:
        return [j for j in self.jobs() if j.state in ACTIVE]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        j = self.get(job_id)
        if j is None or j.state not in ACTIVE:
            return False
        j.cancel.cancel()
        return True

    def cancel_all(self) -> int:
        n = 0
        for j in self.active():
            j.cancel.cancel()
            n += 1
        return n

    def wait_idle(self, timeout: float = 30.0) -> bool:
        t0 = time.time()
        while self.active():
            if time.time() - t0 > timeout:
                return False
            time.sleep(0.02)
        return True

    def run(self, kind: str, label: str, fn: Callable[[Job], object], project: str = "",
            on_done: Callable[[Job, object, BaseException | None], None] | None = None) -> Job:
        """Lance `fn(job)` dans un fil. `fn` met à jour `job.progress` via `registry.update` et surveille `job.cancel`."""
        job = self.create(kind, label, project)

        def _go():
            self.update(job, state="running", started=time.time())
            result, err = None, None
            try:
                result = fn(job)
                self.update(job, state="done", progress=1.0)
            except Cancelled as e:
                err = e
                self.update(job, state="cancelled", message="annulée")
            except BaseException as e:                       # noqa: BLE001 — l'échec d'une tâche ne tue jamais le moteur
                err = e
                self.update(job, state="failed", message=str(e))
            if on_done:
                try:
                    on_done(job, result, err)
                except Exception:                            # noqa: BLE001
                    pass

        threading.Thread(target=_go, name=f"kolnoa-job-{job.id}", daemon=True).start()
        return job
