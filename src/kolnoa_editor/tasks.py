"""Tâches longues en arrière-plan : progression, annulation, journal, erreurs. Indépendant de toute interface.

Une interface (Qt, web local ou terminal) ne fait que :
  - `runner.submit(nom, fonction)` ; la fonction reçoit un `TaskContext` ;
  - écouter les événements (`runner.add_listener`) pour afficher état, progression et journal ;
  - appeler `task.cancel()` pour annuler.
Les rappels (listeners) sont appelés depuis le fil de travail : une interface doit ramener l'événement
sur son propre fil (signal Qt, file d'événements...). Une exception dans un listener ne perturbe jamais la tâche.

Annulation coopérative : le `CancelToken` est celui de `ffmpeg.run`, qui arrête le processus en cours ;
une fonction de tâche appelle aussi `ctx.check_cancelled()` entre deux étapes.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .ffmpeg import CancelToken, Cancelled

log = logging.getLogger(__name__)
MIN_PROGRESS_INTERVAL_S = 0.05   # au plus ~20 mises à jour par seconde (la dernière d'une étape est toujours émise)
MAX_LOG_LINES = 2000


class TaskState(str, Enum):
    QUEUED = "en attente"
    RUNNING = "en cours"
    DONE = "terminée"
    FAILED = "échouée"
    CANCELLED = "annulée"


FINISHED = {TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}


@dataclass(frozen=True)
class Progress:
    stage: str = ""
    current: int = 0
    total: int = 0
    message: str = ""
    eta_s: float | None = None

    @property
    def fraction(self) -> float | None:
        return None if self.total <= 0 else max(0.0, min(1.0, self.current / self.total))


@dataclass(frozen=True)
class TaskEvent:
    task_id: int
    kind: str                       # 'state' | 'progress' | 'log'
    state: TaskState
    progress: Progress | None = None
    message: str = ""


class TaskContext:
    """Ce que voit la fonction d'une tâche."""

    def __init__(self, task: "Task"):
        self._task = task
        self.cancel = task._cancel
        self._stage = ""
        self._stage_start = time.monotonic()
        self._last_emit = 0.0

    def check_cancelled(self) -> None:
        self.cancel.raise_if_cancelled()

    def report(self, stage: str, current: int, total: int, message: str = "") -> None:
        now = time.monotonic()
        if stage != self._stage:
            self._stage, self._stage_start, self._last_emit = stage, now, 0.0
        eta = None
        if total > 0 and 0 < current < total:
            eta = (now - self._stage_start) / current * (total - current)
        last = total > 0 and current >= total
        if not last and now - self._last_emit < MIN_PROGRESS_INTERVAL_S and current != 0:
            self._task._set_progress(Progress(stage, current, total, message, eta), emit=False)
            return
        self._last_emit = now
        self._task._set_progress(Progress(stage, current, total, message, eta), emit=True)

    def log(self, message: str) -> None:
        self._task._add_log(message)


class Task:
    def __init__(self, runner: "TaskRunner", task_id: int, name: str):
        self.id, self.name = task_id, name
        self._runner = runner
        self._lock = threading.RLock()
        self._done = threading.Event()
        self._cancel = CancelToken()
        self.state = TaskState.QUEUED
        self.progress = Progress()
        self.result: Any = None
        self.error: str | None = None
        self.exception: BaseException | None = None   # l'exception d'origine (la CLI la relance telle quelle)
        self.logs: list[str] = []
        self.created = time.monotonic()
        self.started: float | None = None
        self.finished: float | None = None

    # --- côté interface -----------------------------------------------------
    @property
    def is_finished(self) -> bool:
        return self.state in FINISHED

    @property
    def elapsed_s(self) -> float:
        if self.started is None:
            return 0.0
        return (self.finished or time.monotonic()) - self.started

    def cancel(self) -> None:
        """Demande l'annulation : arrête le processus en cours, ou empêche le démarrage si la tâche attend."""
        self._cancel.cancel()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    # --- côté exécution -----------------------------------------------------
    def _emit(self, kind: str, message: str = "") -> None:
        self._runner._dispatch(TaskEvent(self.id, kind, self.state, self.progress if kind == "progress" else None, message))

    def _set_state(self, state: TaskState, message: str = "") -> None:
        with self._lock:
            self.state = state
            if state is TaskState.RUNNING:
                self.started = time.monotonic()
            if state in FINISHED:
                self.finished = time.monotonic()
        self._emit("state", message)
        if state in FINISHED:
            self._done.set()

    def _set_progress(self, p: Progress, emit: bool) -> None:
        with self._lock:
            self.progress = p
        if emit:
            self._emit("progress", p.message)

    def _add_log(self, message: str) -> None:
        with self._lock:
            self.logs.append(message)
            if len(self.logs) > MAX_LOG_LINES:
                del self.logs[: len(self.logs) - MAX_LOG_LINES]
        self._emit("log", message)

    def _run(self, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
        if self._cancel.is_set():                      # annulée avant d'avoir démarré : on ne lance rien
            self._set_state(TaskState.CANCELLED, "annulée avant le démarrage")
            return
        self._set_state(TaskState.RUNNING)
        ctx = TaskContext(self)
        try:
            self.result = fn(ctx, *args, **kwargs)
        except Cancelled:
            self._set_state(TaskState.CANCELLED, "annulée")
        except Exception as exc:                       # noqa: BLE001 - toute erreur de tâche est rapportée, jamais perdue
            self.error = f"{type(exc).__name__}: {exc}"
            self.exception = exc
            self._add_log(traceback.format_exc())
            self._set_state(TaskState.FAILED, self.error)
        else:
            if self._cancel.is_set():
                self._set_state(TaskState.CANCELLED, "annulée")
            else:
                self._set_state(TaskState.DONE)


class TaskRunner:
    """Exécute les tâches dans des fils de travail. Par défaut une seule à la fois (les analyses lourdes
    saturent déjà le disque et le processeur)."""

    def __init__(self, max_workers: int = 1):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="kolnoa-task")
        self._ids = itertools.count(1)
        self._listeners: list[Callable[[TaskEvent], None]] = []
        self._lock = threading.Lock()
        self.tasks: list[Task] = []

    def add_listener(self, fn: Callable[[TaskEvent], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[TaskEvent], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def _dispatch(self, ev: TaskEvent) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(ev)
            except Exception:                           # noqa: BLE001 - un listener défaillant ne casse jamais la tâche
                log.exception("listener de tâche en erreur")

    def submit(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Task:
        task = Task(self, next(self._ids), name)
        with self._lock:
            self.tasks.append(task)
        task._emit("state", "créée")
        self._pool.submit(task._run, fn, args, kwargs)
        return task

    def cancel_all(self) -> None:
        for t in list(self.tasks):
            if not t.is_finished:
                t.cancel()

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=False)
