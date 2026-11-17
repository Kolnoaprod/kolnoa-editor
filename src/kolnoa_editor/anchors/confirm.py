"""Contrat `ConfirmedTarget` (A1-2) : seul moyen d'obtenir une cible APPLICABLE. Non contournable.

  - Le constructeur est protégé par un sentinelle privé : `ConfirmedTarget(...)` hors de ce module lève `TypeError`.
  - `Resolution.confirm(choix)` est la seule fabrique ; elle exige le bon choix selon l'état.
  - `perdue` et `non_supportée` ne se confirment JAMAIS ; `hors_plan` seulement par `AcceptNearestEdge` explicite.
  - Une cible porte l'empreinte de la timeline : `Resolver.check_fresh` refuse de l'appliquer si le montage a changé depuis."""

from __future__ import annotations

from dataclasses import dataclass

_KEY = object()


class ConfirmationRefused(ValueError):
    """Confirmation impossible ou choix inadapté : rien n'est appliqué."""


class StaleTarget(ConfirmationRefused):
    """La timeline a changé depuis la résolution : résoudre de nouveau."""


@dataclass(frozen=True)
class ChooseOccurrence:
    index: int


@dataclass(frozen=True)
class AcceptFragments:
    """J'accepte que la cible soit la collection de morceaux trouvés (couverture partielle/répartie)."""


@dataclass(frozen=True)
class AcceptNearestEdge:
    """J'accepte le bord de plan le plus proche comme cible d'un point hors plan."""


@dataclass(frozen=True)
class Acknowledge:
    """J'ai vu que le voisinage a changé et je confirme la cible."""


@dataclass(frozen=True, init=False)
class ConfirmedTarget:
    anchor_id: str
    segments: tuple          # tuple[Segment, ...] (type vu dans resolver)
    via: str                 # 'exacte' | 'occurrence' | 'fragments' | 'bord_proche' | 'contexte'
    state: str               # état de la résolution confirmée
    token: str               # empreinte de la timeline au moment de la résolution

    def __init__(self, key, anchor_id, segments, via, state, token):
        if key is not _KEY:
            raise TypeError("ConfirmedTarget ne se construit que via Resolution.confirm()")
        for n, v in (("anchor_id", anchor_id), ("segments", tuple(segments)), ("via", via), ("state", state), ("token", token)):
            object.__setattr__(self, n, v)


def confirm(res, choice=None) -> ConfirmedTarget:
    from .resolver import CONTEXTE, EXACTE, FRAGMENTEE, HORS_PLAN, MULTIPLE, NON_SUPPORTEE, PERDUE, Segment
    st = res.state
    if st in (PERDUE, NON_SUPPORTEE):
        raise ConfirmationRefused(f"{st} : jamais de cible appliquable ({res.reason})")
    if st == EXACTE:
        if choice is not None:
            raise ConfirmationRefused("résolution exacte : aucun choix à fournir")
        return ConfirmedTarget(_KEY, res.anchor_id, res.options[0], "exacte", st, res.token)
    if st == MULTIPLE:
        if not isinstance(choice, ChooseOccurrence) or not 0 <= choice.index < len(res.options):
            raise ConfirmationRefused(f"{len(res.options)} emplacements possibles : ChooseOccurrence(index) requis")
        return ConfirmedTarget(_KEY, res.anchor_id, res.options[choice.index], "occurrence", st, res.token)
    if st == FRAGMENTEE:
        if not isinstance(choice, AcceptFragments):
            raise ConfirmationRefused("plage fragmentée : AcceptFragments() requis")
        return ConfirmedTarget(_KEY, res.anchor_id, tuple(s for o in res.options for s in o), "fragments", st, res.token)
    if st == CONTEXTE:
        if not isinstance(choice, Acknowledge):
            raise ConfirmationRefused("contexte modifié : Acknowledge() requis")
        return ConfirmedTarget(_KEY, res.anchor_id, res.options[0], "contexte", st, res.token)
    if st == HORS_PLAN:
        if not isinstance(choice, AcceptNearestEdge) or res.nearest is None:
            raise ConfirmationRefused("hors plan : AcceptNearestEdge() requis (et un bord de plan doit exister)")
        n = res.nearest
        return ConfirmedTarget(_KEY, res.anchor_id, (Segment(n.clip_id, n.track, n.frame, n.frame + 1, n.us, n.us + 1),),
                               "bord_proche", st, res.token)
    raise ConfirmationRefused(f"état inconnu {st}")


def check_fresh(target: ConfirmedTarget, resolver) -> None:
    """À appeler juste avant d'appliquer : refuse une cible calculée sur une timeline qui a changé depuis."""
    if target.token != resolver.token or not resolver.is_fresh():
        raise StaleTarget("la timeline a changé depuis la résolution : résoudre et confirmer de nouveau")
