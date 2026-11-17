"""Horloge maître du lecteur : l'UNIQUE référence de temps. Les lecteurs d'angles s'y asservissent, jamais l'inverse
(pas de dérive cumulée : la position est calculée à partir d'un point d'ancrage, pas incrémentée image par image)."""

from __future__ import annotations

import time
from typing import Callable


class MasterClock:
    def __init__(self, now: Callable[[], float] = time.monotonic):
        self._now = now
        self._anchor_wall = now()
        self._anchor_pos = 0.0
        self._rate = 0.0          # 0 = pause
        self._play_rate = 1.0

    @property
    def playing(self) -> bool:
        return self._rate != 0.0

    @property
    def rate(self) -> float:
        return self._play_rate

    def position(self) -> float:
        return self._anchor_pos + (self._now() - self._anchor_wall) * self._rate

    def _reanchor(self) -> None:
        self._anchor_pos = self.position()
        self._anchor_wall = self._now()

    def seek(self, t: float) -> None:
        self._anchor_pos = max(0.0, t)
        self._anchor_wall = self._now()

    def play(self, rate: float | None = None) -> None:
        self._reanchor()
        if rate is not None:
            if rate <= 0:
                raise ValueError("vitesse de lecture > 0 attendue (la lecture arrière viendra plus tard)")
            self._play_rate = rate
        self._rate = self._play_rate

    def pause(self) -> None:
        self._reanchor()
        self._rate = 0.0
