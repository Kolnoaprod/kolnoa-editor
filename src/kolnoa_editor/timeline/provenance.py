"""Origine des modifications, état des plans et garde de portée (étape 1-a).

Trois « auteurs » modifient la MÊME timeline par la MÊME `Session` : la souris/le clavier (`manuel`), le monteur automatique
(`auto`) et les consignes ciblées (`directive`). Chaque plan a un état :
    auto    produit par le monteur ou une directive, jamais retouché à la main
    manuel  créé ou modifié à la main — AINSI QUE tout plan présent au départ (protégé par défaut)
    valide  accepté explicitement par le réalisateur (verrou doux)
`manuel` et `valide` sont PROTÉGÉS : une transaction `auto`/`directive` ne peut ni les modifier ni les supprimer hors d'une confirmation
explicite. Toute transaction non manuelle DOIT déclarer une `Scope` (zone d'images + pistes) : rien ne peut sortir de cette zone,
hors décalages purs déclarés (`ripple=True`). Tout est vérifié dans `Session.do`, donc impossible à contourner par l'appelant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import Clip, Timeline, TimelineError
from .ops import Op, register

ORIGINS = ("manuel", "auto", "directive")
AUTO, MANUEL, VALIDE = "auto", "manuel", "valide"
STATES = (AUTO, MANUEL, VALIDE)
PROTECTED = (MANUEL, VALIDE)
DEFAULT_STATE = MANUEL          # un plan inconnu de la carte (projet importé, session antérieure) est protégé


class ScopeViolation(TimelineError):
    """Une transaction automatique sort de sa zone ou touche un plan protégé : rien n'a été appliqué."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems[:6]) + (f" (+{len(problems) - 6})" if len(problems) > 6 else ""))
        self.problems = problems


@dataclass(frozen=True)
class Scope:
    """Zone d'une transaction non manuelle : images [start, end) et pistes (None = toutes)."""
    start: int
    end: int
    tracks: tuple[str, ...] | None = None
    ripple: bool = False          # autorise les décalages PURS (début seul modifié) de plans hors zone, p. ex. refermer un trou

    def __post_init__(self):
        if self.end <= self.start:
            raise ValueError("portée vide ou inversée")

    def contains(self, start: int, end: int, track: str) -> bool:
        return start >= self.start and end <= self.end and (self.tracks is None or track in self.tracks)


@register
@dataclass
class Mark(Op):
    """Marque des plans comme `valide` (ou `manuel`) sans toucher la timeline. L'état est géré par la session (annulable)."""
    type = "mark"
    ids: list = field(default_factory=list)
    state: str = VALIDE

    def apply(self, tl: Timeline) -> "Mark":
        if self.state not in (VALIDE, MANUEL):
            raise TimelineError(f"état de marquage invalide : {self.state!r}")
        for i in self.ids:
            tl.clip(i)
        return Mark(list(self.ids), self.state)

    def describe(self) -> str:
        return f"marquer {len(self.ids)} plan(s) « {self.state} »"


def clip_ids_in(obj: Any, out: set[str]) -> None:
    """Toutes les chaînes d'une opération sérialisée : candidates à être des ids de plans (filtrées ensuite)."""
    if isinstance(obj, str):
        out.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            clip_ids_in(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            clip_ids_in(v, out)


def clip_sig(c: Clip) -> tuple:
    d = c.__dict__
    return tuple((k, repr(v) if isinstance(v, (list, dict)) else v) for k, v in d.items())


def snapshot(tl: Timeline, ids: set[str]) -> dict[str, tuple | None]:
    have = {c.id: c for c in tl.clips if c.id in ids}
    return {i: (clip_sig(have[i]), have[i].start, have[i].end, have[i].track) if i in have else None for i in ids}


def snapshot_all(tl: Timeline) -> dict[str, tuple | None]:
    return {c.id: (clip_sig(c), c.start, c.end, c.track) for c in tl.clips}


@dataclass
class Change:
    id: str
    kind: str                       # 'added' | 'removed' | 'changed' | 'shifted'
    before: tuple | None
    after: tuple | None


def changes(before: dict, after: dict) -> list[Change]:
    out = []
    for i in before.keys() | after.keys():
        b, a = before.get(i), after.get(i)
        if b == a:
            continue
        if b is None:
            out.append(Change(i, "added", None, a))
        elif a is None:
            out.append(Change(i, "removed", b, None))
        else:
            # décalage pur : seul `start` diffère (même piste, même durée, même contenu)
            sb = dict(b[0]); sa = dict(a[0])
            sb.pop("start", None); sa.pop("start", None)
            out.append(Change(i, "shifted" if sb == sa and b[3] == a[3] else "changed", b, a))
    return sorted(out, key=lambda c: c.id)


def check_scope(chs: list[Change], scope: Scope | None, states: dict[str, str], confirmed_protected: bool) -> list[str]:
    problems: list[str] = []
    if not chs:
        return []                      # rien n'a changé dans les plans (p. ex. réglage de synchro d'un groupe) : aucune zone à garder
    if scope is None:
        return ["une transaction automatique doit déclarer sa portée (Scope)"]
    for c in chs:
        for tag, snap in (("avant", c.before), ("après", c.after)):
            if snap is None:
                continue
            _, s, e, t = snap
            if scope.contains(s, e, t):
                continue
            if c.kind == "shifted" and scope.ripple:
                continue
            problems.append(f"{c.id} sort de la portée {scope.start}–{scope.end} ({tag} : {s}–{e} sur {t})")
        if c.kind != "added" and not (c.kind == "shifted" and scope.ripple):
            if states.get(c.id, DEFAULT_STATE) in PROTECTED and not confirmed_protected:
                problems.append(f"{c.id} est un plan {states.get(c.id, DEFAULT_STATE)} : confirmation requise")
    return problems
