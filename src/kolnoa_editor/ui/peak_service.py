"""Formes d'onde asynchrones : calcul en arrière-plan, cache disque, affichage progressif. Le fil d'interface ne calcule rien.

- `get(media)` ne bloque JAMAIS : renvoie la pyramide disponible (complète, partielle ou None) et planifie le calcul au besoin.
- Calcul : ffmpeg décode l'audio en flux ; toutes les ~10 s de média on publie une pyramide partielle (`complete=False`),
  si bien que la forme d'onde se remplit de gauche à droite pendant le calcul.
- Priorité : `want(medias)` (les rushes visibles) passe devant la file ; au plus `workers` calculs en parallèle (2 par défaut).
- Cache disque : un `.npy` par rush (clé = chemin + taille + date + version), écriture atomique, fichier abîmé → recalcul.
  Ne touche jamais au média source. Un rush sans audio est mémorisé (pas de relance à chaque dessin).
- `changed(media)` est émis (au plus ~6 fois/s par rush) pour que la vue invalide ses images et redessine.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, Signal

from .peaks import BIN_SAMPLES, SAMPLE_RATE, PeakPyramid, samples_to_bins

CACHE_VERSION = 1
CHUNK_SECONDS = 10
EMIT_EVERY_S = 0.15


def stream_peaks(path: str, on_bins: Callable[[np.ndarray], None], cancel: threading.Event, proc_holder: list) -> bool:
    """Décode `path` avec ffmpeg et appelle `on_bins(nouvelles crêtes)` par tranches. True si le flux est allé au bout."""
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    try:
        proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kw)
    except OSError:
        return False
    proc_holder.append(proc)
    chunk_bytes = SAMPLE_RATE * CHUNK_SECONDS * 4
    leftover = b""
    got = False
    while not cancel.is_set():
        data = proc.stdout.read(chunk_bytes)
        if not data:
            break
        data = leftover + data
        usable = len(data) // (BIN_SAMPLES * 4) * (BIN_SAMPLES * 4)
        leftover = data[usable:]
        if usable:
            got = True
            on_bins(samples_to_bins(np.frombuffer(data[:usable], dtype=np.float32)))
    if cancel.is_set():
        proc.kill()
        proc.wait()
        return False
    return proc.wait() == 0 and got


class PeakService(QObject):
    changed = Signal(str)          # media (chemin tel que donné à get)

    def __init__(self, cache_dir: Path | None = None, resolver: Callable[[str], str] | None = None, workers: int = 2,
                 compute=stream_peaks, parent=None):
        super().__init__(parent)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.resolver = resolver or (lambda m: m)
        self._compute = compute
        self._lock = threading.Lock()
        self._pyr: dict[str, PeakPyramid] = {}
        self._none: set[str] = set()                  # rushs sans audio / illisibles
        self._queue: dict[str, int] = {}              # media → priorité (plus petit = plus tôt)
        self._running: set[str] = set()
        self._progress: dict[str, float] = {}
        self._wanted: list[str] = []
        self._cancel = threading.Event()
        self._procs: list = []
        self._wake = threading.Condition(self._lock)
        self._seq = 1000
        self.stats = {"computed": 0, "from_cache": 0, "cancelled": 0}
        self._threads = [threading.Thread(target=self._worker, daemon=True, name=f"peaks-{i}") for i in range(max(1, workers))]
        for t in self._threads:
            t.start()

    # --- API de la vue (jamais bloquante) -------------------------------------------------------------------
    def get(self, media: str) -> PeakPyramid | None:
        with self._lock:
            p = self._pyr.get(media)
            if p is not None or media in self._none:
                return p
            if media not in self._queue and media not in self._running:
                self._seq += 1
                self._queue[media] = self._seq
                self._wake.notify()
        return None

    def want(self, medias: list[str]) -> None:
        """Les rushs actuellement visibles passent devant la file (ordre = priorité)."""
        with self._lock:
            if medias == self._wanted:
                return
            self._wanted = list(medias)
            for i, m in enumerate(medias):
                if m in self._queue:
                    self._queue[m] = i

    def progress(self, media: str) -> float | None:
        """0..1 pendant le calcul, 1.0 terminé, None inconnu (jamais demandé)."""
        with self._lock:
            if media in self._pyr and self._pyr[media].complete:
                return 1.0
            return self._progress.get(media)

    def idle(self) -> bool:
        with self._lock:
            return not self._queue and not self._running

    def close(self) -> None:
        self._cancel.set()
        with self._lock:
            self._wake.notify_all()
            procs = list(self._procs)
        for p in procs:
            try:
                p.kill()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=3)

    # --- cache disque ---------------------------------------------------------------------------------------
    def _key(self, path: str) -> Path | None:
        if not self.cache_dir:
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        h = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{CACHE_VERSION}".encode()).hexdigest()[:24]
        return self.cache_dir / f"{h}.npy"

    def _load_cache(self, path: str) -> np.ndarray | None:
        f = self._key(path)
        if f is None or not f.is_file():
            return None
        try:
            a = np.load(f, allow_pickle=False)
            return a if a.dtype == np.uint8 and a.ndim == 1 and len(a) else None
        except (OSError, ValueError):
            try:
                f.unlink()
            except OSError:
                pass
            return None

    def _save_cache(self, path: str, bins: np.ndarray) -> None:
        f = self._key(path)
        if f is None:
            return
        tmp = f.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "wb") as fh:
                np.save(fh, bins, allow_pickle=False)
            os.replace(tmp, f)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

    # --- fils de calcul -------------------------------------------------------------------------------------
    def _next(self) -> str | None:
        with self._lock:
            while not self._queue and not self._cancel.is_set():
                self._wake.wait(0.5)
            if self._cancel.is_set():
                return None
            media = min(self._queue, key=self._queue.get)
            del self._queue[media]
            self._running.add(media)
            return media

    def _worker(self) -> None:
        while True:
            media = self._next()
            if media is None:
                return
            try:
                self._process(media)
            finally:
                with self._lock:
                    self._running.discard(media)

    def _process(self, media: str) -> None:
        path = self.resolver(media)
        cached = self._load_cache(path)
        if cached is not None:
            with self._lock:
                self._pyr[media] = PeakPyramid(cached, True, 1)
                self.stats["from_cache"] += 1
            self.changed.emit(media)
            return
        total = self._guess_total(path)
        parts: list[np.ndarray] = []
        rev = [0]
        last_emit = [0.0]

        def publish(final: bool = False) -> None:
            bins = np.concatenate(parts) if parts else np.zeros(0, dtype=np.uint8)
            rev[0] += 1
            pyr = PeakPyramid(bins, final, rev[0])
            with self._lock:
                self._pyr[media] = pyr
            self.changed.emit(media)

        def on_bins(b: np.ndarray) -> None:
            parts.append(b)
            now = time.monotonic()
            with self._lock:
                self._progress[media] = min(0.99, sum(len(x) for x in parts) / total)
            if now - last_emit[0] >= EMIT_EVERY_S:
                last_emit[0] = now
                publish(False)

        ok = self._compute(path, on_bins, self._cancel, self._procs)
        if self._cancel.is_set():
            with self._lock:
                self.stats["cancelled"] += 1
            return
        if not ok or not parts:
            with self._lock:
                self._none.add(media)
                self._pyr.pop(media, None)
            self.changed.emit(media)
            return
        bins = np.concatenate(parts)
        self._save_cache(path, bins)
        with self._lock:
            self.stats["computed"] += 1
            self._progress[media] = 1.0
        publish(True)

    def _guess_total(self, path: str) -> int:
        """Nombre de crêtes attendu (pour la progression) : durée du conteneur si ffprobe répond, sinon l'estimation grossière 1 h."""
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                 capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL).stdout.strip()
            return max(1, int(float(out) / (BIN_SAMPLES / SAMPLE_RATE)))
        except (OSError, ValueError, subprocess.SubprocessError):
            return 180000
