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
from collections import OrderedDict
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
DEFAULT_RAM_BYTES = 128 * 1024 * 1024        # plafond des pyramides en mémoire (≈ 1,3 M s de média ; LRU)
DEFAULT_DISK_BYTES = 1024 * 1024 * 1024      # plafond du cache disque
DEFAULT_DISK_DAYS = 90                       # entrées plus anciennes (dernier accès) purgées
RECHECK_S = 1.0                              # un rush déjà en mémoire est re-stat() au plus toutes les secondes


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


def _unlink(f: Path) -> int:
    try:
        f.unlink()
        return 1
    except OSError:
        return 0


class _JobCancel:
    """Annulation d'UN calcul : arrêt global du service, ou le rush a changé (génération différente) → le résultat serait périmé."""

    def __init__(self, svc: "PeakService", media: str, gen: int):
        self.svc, self.media, self.gen = svc, media, gen

    def is_set(self) -> bool:
        return self.svc._cancel.is_set() or self.svc._gen.get(self.media, 0) != self.gen

    def wait(self, t=None):
        return self.is_set()


class PeakService(QObject):
    changed = Signal(str)          # media (chemin tel que donné à get)

    def __init__(self, cache_dir: Path | None = None, resolver: Callable[[str], str] | None = None, workers: int = 2,
                 compute=stream_peaks, parent=None, max_ram_bytes: int = DEFAULT_RAM_BYTES, recheck_s: float = RECHECK_S,
                 purge_on_start: bool = True, disk_bytes: int = DEFAULT_DISK_BYTES, disk_days: float = DEFAULT_DISK_DAYS):
        super().__init__(parent)
        self.max_ram_bytes, self.recheck_s = max_ram_bytes, recheck_s
        self.disk_bytes, self.disk_days = disk_bytes, disk_days
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.resolver = resolver or (lambda m: m)
        self._compute = compute
        self._lock = threading.Lock()
        self._pyr: OrderedDict[str, PeakPyramid] = OrderedDict()   # LRU : le plus récemment utilisé à la fin
        self._sig: dict[str, tuple] = {}              # media → (taille, mtime_ns) du fichier quand la pyramide a été calculée
        self._checked: dict[str, float] = {}          # media → dernier instant de vérification de la signature
        self._gen: dict[str, int] = {}                # media → génération (change quand le fichier change : résultats périmés jetés)
        self._none: set[str] = set()                  # rushs sans audio / illisibles
        self._queue: dict[str, int] = {}              # media → priorité (plus petit = plus tôt)
        self._running: set[str] = set()
        self._progress: dict[str, float] = {}
        self._wanted: list[str] = []
        self._cancel = threading.Event()
        self._procs: list = []
        self._wake = threading.Condition(self._lock)
        self._seq = 1000
        self.stats = {"computed": 0, "from_cache": 0, "cancelled": 0, "evicted": 0, "invalidated": 0, "discarded_stale": 0, "purged_files": 0}
        self._threads = [threading.Thread(target=self._worker, daemon=True, name=f"peaks-{i}") for i in range(max(1, workers))]
        for t in self._threads:
            t.start()
        if purge_on_start and self.cache_dir:
            threading.Thread(target=self.purge_cache, daemon=True, name="peaks-purge").start()

    # --- signature du fichier / invalidation ---------------------------------------------------------------
    def _file_sig(self, media: str) -> tuple | None:
        try:
            st = os.stat(self.resolver(media))
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    def invalidate(self, media: str) -> None:
        """Le rush a changé (ou l'appelant le sait) : oublie la pyramide en mémoire et l'état « sans audio », jette tout calcul en cours pour ce rush."""
        with self._lock:
            self._invalidate_locked(media)
        self.changed.emit(media)

    def _invalidate_locked(self, media: str) -> None:
        self._pyr.pop(media, None)
        self._none.discard(media)
        self._sig.pop(media, None)
        self._progress.pop(media, None)
        self._gen[media] = self._gen.get(media, 0) + 1
        self.stats["invalidated"] += 1

    def _check_fresh(self, media: str) -> None:
        """Appelé avec le verrou : si le fichier a changé depuis le calcul, la pyramide mémorisée est périmée."""
        now = time.monotonic()
        if now - self._checked.get(media, -1e9) < self.recheck_s:
            return
        self._checked[media] = now
        if media in self._sig and self._file_sig(media) != self._sig[media]:
            self._invalidate_locked(media)

    def _evict_locked(self) -> None:
        total = sum(p.nbytes for p in self._pyr.values())
        for media in list(self._pyr):
            if total <= self.max_ram_bytes:
                break
            if media in self._wanted or media in self._running:
                continue                                  # ce qui est à l'écran ou en calcul n'est jamais évincé
            total -= self._pyr.pop(media).nbytes
            self.stats["evicted"] += 1

    def ram_bytes(self) -> int:
        with self._lock:
            return sum(p.nbytes for p in self._pyr.values())

    # --- API de la vue (jamais bloquante) -------------------------------------------------------------------
    def get(self, media: str) -> PeakPyramid | None:
        with self._lock:
            self._check_fresh(media)
            p = self._pyr.get(media)
            if p is not None or media in self._none:
                if p is not None:
                    self._pyr.move_to_end(media)           # LRU : utilisé à l'instant
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
    def _key(self, path: str, sig: tuple | None) -> Path | None:
        if not self.cache_dir or sig is None:
            return None
        h = hashlib.sha1(f"{os.path.abspath(path)}|{sig[0]}|{sig[1]}|{CACHE_VERSION}".encode()).hexdigest()[:24]
        return self.cache_dir / f"{h}.npy"

    def _load_cache(self, path: str, sig: tuple | None) -> np.ndarray | None:
        f = self._key(path, sig)
        if f is None or not f.is_file():
            return None
        try:
            a = np.load(f, allow_pickle=False)
            if a.dtype != np.uint8 or a.ndim != 1 or not len(a):
                return None
            os.utime(f)                                   # « dernier accès » : la purge garde ce qui sert
            return a
        except (OSError, ValueError):
            try:
                f.unlink()
            except OSError:
                pass
            return None

    def _save_cache(self, path: str, bins: np.ndarray, sig: tuple | None) -> None:
        """La clé vient de la signature du fichier AU DÉBUT du calcul : un rush modifié pendant le calcul ne pollue jamais la nouvelle clé."""
        f = self._key(path, sig)
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

    def purge_cache(self, max_bytes: int | None = None, max_age_days: float | None = None) -> int:
        """Purge du cache disque : supprime les `.tmp` abandonnés (> 1 h), les entrées non utilisées depuis `max_age_days`, puis les plus
        anciennes (dernier accès) tant que le total dépasse `max_bytes`. Renvoie le nombre de fichiers supprimés. Ne touche qu'à `*.npy`/`*.tmp` du dossier de cache."""
        if not self.cache_dir:
            return 0
        max_bytes = self.disk_bytes if max_bytes is None else max_bytes
        max_age = (self.disk_days if max_age_days is None else max_age_days) * 86400
        now, removed, entries = time.time(), 0, []
        try:
            files = list(self.cache_dir.iterdir())
        except OSError:
            return 0
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            if f.suffix == ".tmp":
                if now - st.st_mtime > 3600:
                    removed += _unlink(f)
            elif f.suffix == ".npy":
                if now - st.st_mtime > max_age:
                    removed += _unlink(f)
                else:
                    entries.append((st.st_mtime, st.st_size, f))
        entries.sort()                                    # les plus anciens d'abord
        total = sum(e[1] for e in entries)
        for _, size, f in entries:
            if total <= max_bytes:
                break
            removed += _unlink(f)
            total -= size
        with self._lock:
            self.stats["purged_files"] += removed
        return removed

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
        with self._lock:
            gen = self._gen.get(media, 0)
        sig = self._file_sig(media)                       # signature AU DÉBUT : tout ce qui suit est daté de cet état du fichier
        job = _JobCancel(self, media, gen)

        def stale() -> bool:
            return self._cancel.is_set() or self._gen.get(media, 0) != gen or self._file_sig(media) != sig

        cached = self._load_cache(path, sig)
        if cached is not None:
            with self._lock:
                if self._gen.get(media, 0) == gen:
                    self._pyr[media] = PeakPyramid(cached, True, 1)
                    self._sig[media] = sig
                    self._evict_locked()
                    self.stats["from_cache"] += 1
            self.changed.emit(media)
            return
        total = self._guess_total(path)
        parts: list[np.ndarray] = []
        rev = [0]
        last_emit = [0.0]

        def publish(final: bool = False) -> bool:
            bins = np.concatenate(parts) if parts else np.zeros(0, dtype=np.uint8)
            rev[0] += 1
            pyr = PeakPyramid(bins, final, rev[0])
            with self._lock:
                if self._gen.get(media, 0) != gen:       # le rush a changé entre-temps : résultat périmé, on ne le publie pas
                    return False
                self._pyr[media] = pyr
                self._pyr.move_to_end(media)
                self._sig[media] = sig
                self._evict_locked()
            self.changed.emit(media)
            return True

        def on_bins(b: np.ndarray) -> None:
            parts.append(b)
            now = time.monotonic()
            with self._lock:
                self._progress[media] = min(0.99, sum(len(x) for x in parts) / total)
            if now - last_emit[0] >= EMIT_EVERY_S:
                last_emit[0] = now
                publish(False)

        ok = self._compute(path, on_bins, job, self._procs)
        if self._cancel.is_set():
            with self._lock:
                self.stats["cancelled"] += 1
            return
        if stale():                                       # modifié pendant le calcul : on jette et on relance sur le nouveau fichier
            with self._lock:
                self._gen[media] = self._gen.get(media, 0) + 1
                self._pyr.pop(media, None)
                self._sig.pop(media, None)
                self.stats["discarded_stale"] += 1
                self._seq += 1
                self._queue[media] = self._seq
                self._wake.notify()
            self.changed.emit(media)
            return
        if not ok or not parts:
            with self._lock:
                self._none.add(media)
                self._sig[media] = sig                     # un rush muet qui change (nouvel export) sera revu
                self._pyr.pop(media, None)
            self.changed.emit(media)
            return
        bins = np.concatenate(parts)
        self._save_cache(path, bins, sig)
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
