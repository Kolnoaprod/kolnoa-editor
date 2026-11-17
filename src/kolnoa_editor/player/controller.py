"""Contrôleur du lecteur multicam (sans Qt) : horloge maître + un lecteur par angle + planificateur + prise de touches 1-9.

- `tick()` (appelé par un timer, ~30 Hz) : replanifie si besoin, recale chaque lecteur sur l'horloge (rattrapage de vitesse
  ± quelques %, ou seek si l'écart est grand), et fait l'échange d'affichage « tardif » : un changement d'angle ne bascule
  l'affichage que quand le nouvel angle a une image à l'heure → l'image précédente reste affichée, jamais de gel ni d'écran noir.
- Les touches 1-9 changent l'angle affiché tout de suite (intention) ; l'affichage suit dès que possible (`shown`).
- La prise est enregistrée en mémoire ; `commit()` la valide dans le moteur en UNE Transaction (`record_angle_switches`).
  Rien n'est écrit tant qu'on ne valide pas ; `discard()` jette la prise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..timeline.model import Timeline, TimelineError
from ..timeline.multicam_live import angle_for_key, key_angles, record_angle_switches
from .backend import AngleBackend
from .clock import MasterClock
from .scheduler import ACTIVE, COLD, PAUSED, MediaInfo, Slot, plan

SOFT_ERR = 0.040        # s : en dessous, on ne touche à rien
ACTIVE_SOFT_ERR = 0.020
HARD_ERR = 0.250        # s : au-delà, on re-seeke
NUDGE_GAIN = 1.0        # correction de vitesse = 1 - gain*err (bornée)
MAX_NUDGE = 0.05        # ± 5 % de vitesse au maximum
PAUSED_REFRESH_WALL = 0.5   # s RÉELLES : cadence de ré-alignement d'un angle « en pause » pendant la lecture (coût de décodage = temps réel,
                            # donc indépendant de la vitesse de lecture ; l'écart toléré, lui, reste en secondes MÉDIA)
IDLE_REFRESH_WALL = 0.10    # s réelles : horloge en pause, anti-rafale entre deux re-seeks d'un angle qui n'a pas encore atterri
SEEK_SETTLE_WALL = 0.60     # s réelles : un seek émis est « en vol » ce temps-là (ou jusqu'à ce que le lecteur atterrisse)
SWAP_TOL = 0.080        # s : écart toléré pour afficher l'image d'un angle prêt
SWAP_TIMEOUT = 0.75     # s : si l'angle n'est pas prêt après ce délai, on force le basculement (le temps d'attente est mesuré)


@dataclass
class Stats:
    switch_latencies: list[float] = field(default_factory=list)   # s entre la touche et l'affichage effectif
    forced_swaps: int = 0
    reseeks: int = 0
    nudges: int = 0
    replans: int = 0
    over_budget_replans: int = 0
    seeks_issued: int = 0


class MulticamPlayer:
    def __init__(self, tl: Timeline, group_id: str, track: str, media: dict[str, MediaInfo],
                 backend_factory: Callable[[str], AngleBackend], clock: MasterClock | None = None, *,
                 budget: float = 4e8, hw_decode: bool = False, now: Callable[[], float] | None = None,
                 lookahead: float = 3.0):
        self.tl, self.group_id, self.track = tl, group_id, track
        self.group = tl.group(group_id)
        self.clock = clock or MasterClock()
        self._now = now or self.clock._now
        self.media = media
        self.budget, self.hw_decode, self.lookahead = budget, hw_decode, lookahead
        self.keys = [a for a in key_angles(tl, group_id) if a in media]
        if not self.keys:
            raise TimelineError("aucun angle image lisible dans ce groupe")
        self.backends: dict[str, AngleBackend] = {a: backend_factory(a) for a in media}
        self._open: dict[str, Slot] = {}
        self._last_refresh: dict[str, float] = {}        # instants RÉELS (self._now) du dernier seek par angle
        self._pending: dict[str, tuple[float, float]] = {}   # angle -> (cible média, instant réel d'émission)
        self.report = None
        self.warnings: tuple = ()
        self.intent = self.keys[0]       # angle demandé
        self.shown = self.keys[0]        # angle effectivement affiché
        self._intent_at = self._now()
        self.take: list[tuple[int, str]] = []   # [(image, angle)] — prise en cours, pas encore dans le moteur
        self.recording = False
        self.stats = Stats()
        self.slots: list[Slot] = []
        self._dirty = True

    def _seek(self, a: str, target: float) -> None:
        target = max(0.0, target)
        self.backends[a].seek(target)
        now = self._now()
        self._last_refresh[a] = now
        self._pending[a] = (target, now)
        self.stats.seeks_issued += 1

    def _in_flight(self, a: str, target: float) -> bool:
        """Un seek émis récemment n'a pas encore atterri : on n'en émet pas un second par-dessus (anti-rafale)."""
        pend = self._pending.get(a)
        if not pend:
            return False
        if self._now() - pend[1] >= SEEK_SETTLE_WALL:
            self._pending.pop(a, None)
            return False
        if abs(self.backends[a].position() - target) <= SOFT_ERR:
            self._pending.pop(a, None)
            return False
        return True

    # --- temps -----------------------------------------------------------------------------------------------
    def t_angle(self, angle_id: str, t: float) -> float:
        return self.group.angle(angle_id).to_angle(t)

    def frame(self) -> int:
        return int(self.clock.position() * self.tl.fps)

    def play(self, record: bool = False) -> None:
        self.recording = record
        if record:
            self.take = [(self.frame(), self.intent)]
        self.clock.play()
        self._dirty = True
        self.tick()

    def pause(self) -> None:
        self.clock.pause()
        self._dirty = True
        self.tick()

    def seek(self, t: float) -> None:
        self.clock.seek(t)
        for a in self._open:
            self._seek(a, self.t_angle(a, t))                 # le dernier seek gagne : chacun écrase le « en vol » précédent
        self._dirty = True
        self.tick()

    # --- touches ---------------------------------------------------------------------------------------------
    def press(self, key: int) -> str:
        angle = angle_for_key(self.tl, self.group_id, key)
        if angle not in self.media:
            raise TimelineError(f"l'angle {angle} (touche {key}) n'a pas de média chargé")
        if angle == self.intent:
            return angle
        self.intent = angle
        self._intent_at = self._now()
        if self.recording and self.clock.playing:
            self.take.append((self.frame(), angle))
        self._dirty = True
        self.tick()
        return angle

    def commit(self):
        """Valide la prise dans le moteur : UNE transaction (renvoie-la pour que l'appelant fasse `Session.do`)."""
        tx = record_angle_switches(self.tl, self.track, self.take)
        self.take = []
        self.recording = False
        return tx

    def discard(self) -> None:
        self.take, self.recording = [], False

    # --- boucle ----------------------------------------------------------------------------------------------
    def _upcoming(self) -> list[tuple[float, str]]:
        return [(f / self.tl.fps, a) for f, a in self.take if f / self.tl.fps >= self.clock.position()]

    def _apply_plan(self, t: float) -> None:
        self.slots = plan(self.media, self.intent, self.t_angle, t, budget=self.budget, upcoming=self._upcoming(),
                          lookahead=self.lookahead, hw_decode=self.hw_decode)
        self.stats.replans += 1
        self.report, self.warnings = self.slots, self.slots.warnings
        if self.slots.over_budget:
            self.stats.over_budget_replans += 1
        want = {s.angle: s for s in self.slots}
        for a, s in want.items():
            be, cur = self.backends[a], self._open.get(a)
            if s.quality is None:
                if cur is not None:
                    be.close()
                    del self._open[a]
                continue
            if cur is None or (cur.path, cur.quality) != (s.path, s.quality):
                be.open(s.path, s.quality)
                self._seek(a, self.t_angle(a, t))
            be.set_priority(s.priority)
            self._open[a] = s
        self._dirty = False

    def _discipline(self, a: str, s: Slot, t: float) -> None:
        be = self.backends[a]
        target = self.t_angle(a, t)
        playing = self.clock.playing
        wall = self._now()
        if s.role == PAUSED or not playing:
            # horloge en pause, ou angle « PAUSED » : on le garde à l'arrêt et on le ré-aligne à cadence RÉELLE bornée
            be.pause()
            if abs(be.position() - target) > SOFT_ERR and not self._in_flight(a, target):
                gap = IDLE_REFRESH_WALL if not playing else PAUSED_REFRESH_WALL
                if wall - self._last_refresh.get(a, -1e9) >= gap:
                    self._seek(a, target)
            return
        if self._in_flight(a, target):
            return                                                # un seek vient d'être émis : on attend qu'il atterrisse
        err = be.position() - target
        tol = ACTIVE_SOFT_ERR if s.role == ACTIVE else SOFT_ERR
        if abs(err) > HARD_ERR:
            self._seek(a, target)
            be.play(self.clock.rate)
            self.stats.reseeks += 1
        elif abs(err) > tol:
            adj = max(-MAX_NUDGE, min(MAX_NUDGE, -NUDGE_GAIN * err))
            be.play(self.clock.rate * (1.0 + adj))
            self.stats.nudges += 1
        else:
            be.play(self.clock.rate)

    def tick(self) -> None:
        t = self.clock.position()
        if self._dirty:
            self._apply_plan(t)
        # l'actif d'abord (priorité de traitement), puis le reste
        for s in sorted(self.slots, key=lambda s: s.priority):
            if s.angle in self._open:
                self._discipline(s.angle, s, t)
        # échange d'affichage tardif
        if self.shown != self.intent:
            be = self.backends[self.intent]
            waited = self._now() - self._intent_at
            ready = self.intent in self._open and be.has_frame_near(self.t_angle(self.intent, t), SWAP_TOL)
            if ready or waited >= SWAP_TIMEOUT:
                if not ready:
                    self.stats.forced_swaps += 1
                self.stats.switch_latencies.append(waited)
                self.shown = self.intent

    def close(self) -> None:
        for a in list(self._open):
            self.backends[a].close()
        self._open.clear()
