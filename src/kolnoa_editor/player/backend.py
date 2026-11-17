"""Contrat d'un lecteur d'angle + un faux lecteur déterministe pour les tests (aucune vidéo, aucun Qt)."""

from __future__ import annotations

from typing import Protocol


class AngleBackend(Protocol):
    def open(self, path: str, quality: str) -> None: ...
    def close(self) -> None: ...
    def seek(self, t: float) -> None: ...
    def play(self, rate: float) -> None: ...
    def pause(self) -> None: ...
    def position(self) -> float: ...
    def has_frame_near(self, t: float, tol: float) -> bool: ...   # une image proche de t est prête à afficher
    def set_priority(self, level: int) -> None: ...                # 0 = actif (le plus prioritaire)


class FakeBackend:
    """Simule un décodeur : seek = latence `seek_s` avant qu'une image soit prête ; lecture à vitesse réelle ± `speed_err`.
    Le temps vient de `clock_fn` (le même faux temps que l'horloge maître)."""

    def __init__(self, now, seek_s: float = 0.15, speed_err: float = 0.0, duration: float = 1e9):
        self.now, self.seek_s, self.speed_err, self.duration = now, seek_s, speed_err, duration
        self.path = None
        self.quality = None
        self.priority = 9
        self._pos, self._wall, self._rate = 0.0, now(), 0.0
        self._ready_at = 0.0
        self.log: list[tuple] = []

    def open(self, path, quality):
        self.path, self.quality = path, quality
        self._pos, self._wall, self._rate = 0.0, self.now(), 0.0
        self._ready_at = self.now() + self.seek_s
        self.log.append(("open", path, quality))

    def close(self):
        self.path = None
        self.log.append(("close",))

    def _sync(self):
        t = self.now()
        self._pos += (t - self._wall) * self._rate * (1.0 + self.speed_err)
        self._wall = t

    def seek(self, t):
        self._sync()
        self._pos = t
        self._ready_at = self.now() + self.seek_s
        self.log.append(("seek", round(t, 3)))

    def play(self, rate):
        self._sync()
        self._rate = rate
        self.log.append(("play", rate))

    def pause(self):
        self._sync()
        self._rate = 0.0
        self.log.append(("pause",))

    def position(self):
        self._sync()
        return self._pos

    def has_frame_near(self, t, tol):
        return self.path is not None and self.now() >= self._ready_at and abs(self.position() - t) <= tol

    def set_priority(self, level):
        self.priority = level
