"""Planificateur de décodage : décide, pour chaque angle, ce qu'on décode et avec quelle qualité.

Rôles :
  ACTIVE — l'angle affiché : décodé en priorité 0, qualité maximale que le budget permet.
  NEXT   — angle qui sera affiché dans la fenêtre d'anticipation (coupes déjà enregistrées/prévues) : lu en proxy, synchronisé.
  WARM   — lu en proxy, synchronisé (changement d'angle instantané : l'image est déjà là).
  PAUSED — ouvert mais en pause, ré-aligné de temps en temps (coût quasi nul ; un changement d'angle attend une image fraîche).
  COLD   — fermé (hors budget, ou angle pas encore commencé / terminé à cet instant).
Le coût est estimé en pixels/s décodés (largeur×hauteur×fps) ; budget = capacité décodage de la machine (mesurée par le bench).
Fonction pure : mêmes entrées → mêmes sorties, testable sans vidéo.
"""

from __future__ import annotations

from dataclasses import dataclass

ACTIVE, NEXT, WARM, PAUSED, COLD = "active", "next", "warm", "paused", "cold"


@dataclass(frozen=True)
class MediaInfo:
    """Ce que le planificateur sait d'un angle : source et proxy éventuel (dimensions + fps)."""
    source: str
    width: int
    height: int
    fps: float
    proxy: str | None = None
    proxy_height: int = 540
    duration: float | None = None      # secondes d'angle ; None = inconnu

    @property
    def proxy_cost(self) -> float:
        p = self.proxy_height * self.width / max(1, self.height)
        return p * self.proxy_height * min(self.fps, 30)   # le proxy est limité à 30 i/s pour la prévisualisation

    @property
    def source_cost(self) -> float:
        return self.width * self.height * self.fps


@dataclass(frozen=True)
class Slot:
    angle: str
    role: str
    quality: str | None        # 'source' | 'proxy' | None (fermé)
    path: str | None
    priority: int              # 0 = le plus prioritaire
    cost: float


class Plan(list):
    """Liste de `Slot` + compte rendu : le budget dépassé ou un proxy manquant n'est JAMAIS silencieux."""
    spent: float = 0.0
    budget: float = 0.0
    over_budget: bool = False
    missing_proxy: tuple = ()
    warnings: tuple = ()


def plan(angles: dict[str, MediaInfo], active: str, t_group_to_angle, t: float, *, budget: float,
         upcoming: list[tuple[float, str]] = (), lookahead: float = 3.0, hw_decode: bool = False,
         allow_source: bool = True, max_warm: int = 9) -> Plan:
    """`t_group_to_angle(angle_id, t)` → temps dans l'angle. `upcoming` = [(t_groupe, angle)] coupes à venir."""
    if active not in angles:
        raise KeyError(f"angle actif inconnu : {active}")

    def present(a: str) -> bool:
        info = angles[a]
        ta = t_group_to_angle(a, t)
        return ta >= 0 and (info.duration is None or ta < info.duration)

    soon = {a for (tg, a) in upcoming if t <= tg <= t + lookahead}
    spent = 0.0
    slots: dict[str, Slot] = {}

    # 1) l'angle actif : source seulement si le matériel décode (ou budget très large) ET qu'il reste de quoi tenir les autres en proxy
    act = angles[active]
    others = [a for a in angles if a != active and present(a)]
    reserve = sum(angles[a].proxy_cost for a in others[:max_warm - 1] if angles[a].proxy)
    use_source = allow_source and (hw_decode or act.source_cost <= budget * 0.5) and act.source_cost + reserve <= budget
    warnings: list[str] = []
    missing: list[str] = [a for a, i in angles.items() if i.proxy is None and present(a)]
    if act.proxy is None or use_source:
        q, path, cost = "source", act.source, act.source_cost
        if act.proxy is None and act.source_cost > budget:
            warnings.append(f"{active}: pas de proxy et la source ({act.source_cost:.2e} px/s) dépasse le budget ({budget:.2e}) : lecture fluide non garantie")
    else:
        q, path, cost = "proxy", act.proxy, act.proxy_cost
    if not present(active):
        slots[active] = Slot(active, COLD, None, None, 0, 0.0)
    else:
        slots[active] = Slot(active, ACTIVE, q, path, 0, cost)
        spent += cost

    # 2) les autres : NEXT d'abord (par échéance), puis WARM, tant que le budget tient ; le reste en PAUSED (coût ~0) ou COLD
    order = sorted(others, key=lambda a: (a not in soon,
                                          min((tg for tg, x in upcoming if x == a and tg >= t), default=1e18), a))
    warm_count = 0
    for a in order:
        info = angles[a]
        path = info.proxy or info.source
        c = info.proxy_cost if info.proxy else info.source_cost
        prio = 1 if a in soon else 2
        if spent + c <= budget and warm_count < max_warm - 1:
            slots[a] = Slot(a, NEXT if a in soon else WARM, "proxy" if info.proxy else "source", path, prio, c)
            spent += c
            warm_count += 1
        elif a in soon:
            # une coupe est imminente mais le budget est plein : on l'ouvre quand même, en pause, déjà positionné
            slots[a] = Slot(a, PAUSED, "proxy" if info.proxy else "source", path, 3, 0.0)
        else:
            slots[a] = Slot(a, PAUSED, "proxy" if info.proxy else "source", path, 4, 0.0)
    for a in angles:
        slots.setdefault(a, Slot(a, COLD, None, None, 9, 0.0))
    out = Plan(slots[a] for a in angles)
    out.spent, out.budget = spent, budget
    out.over_budget = spent > budget + 1e-9
    for a in missing:
        if a != active:
            sl = slots[a]
            if sl.role == PAUSED:
                warnings.append(f"{a}: pas de proxy et la source ne tient pas dans le budget : angle en pause (ré-aligné, pas lu en continu)")
            else:
                warnings.append(f"{a}: pas de proxy, lu en source ({sl.cost:.2e} px/s)")
    out.missing_proxy = tuple(missing)
    if out.over_budget:
        warnings.append(f"budget dépassé : {spent:.2e} > {budget:.2e} px/s")
    out.warnings = tuple(warnings)
    return out
