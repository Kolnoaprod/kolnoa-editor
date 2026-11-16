"""Multicam « à la main » : touches 1 à 9 pendant la lecture, comme dans Premiere ou Final Cut.

Appuyer sur la touche N à l'image `f` coupe le plan multicam de la piste visée à `f` et montre l'angle N à partir de
là (jusqu'à la fin de ce plan, ou jusqu'à la prochaine touche). Le son ne bouge pas : il vient du maître du groupe.
Une prise complète (plusieurs touches) = UNE Transaction = un seul pas d'annulation. Rien n'est lu sur disque.
"""

from __future__ import annotations

from .editing import razor
from .model import Clip, Timeline, TimelineError
from .ops import Op, Transaction, execute, switch_angle

MAX_KEYS = 9


def key_angles(tl: Timeline, group_id: str) -> list[str]:
    """Angles image du groupe dans l'ordre des touches : touche 1 = premier angle vidéo, etc. (l'audio seul n'a pas de touche)."""
    return [a.id for a in tl.group(group_id).angles if a.kind == "video"][:MAX_KEYS]


def angle_for_key(tl: Timeline, group_id: str, key: int) -> str:
    keys = key_angles(tl, group_id)
    if not 1 <= key <= MAX_KEYS or key > len(keys):
        raise TimelineError(f"la touche {key} n'a pas d'angle dans le groupe {group_id} ({len(keys)} angle(s) image)")
    return keys[key - 1]


def clip_at(tl: Timeline, track: str, frame: int) -> Clip:
    for c in tl.clips_on(track):
        if c.start <= frame < c.end:
            return c
    raise TimelineError(f"aucun plan sur {track} à l'image {frame}")


def _cut_ops(work: Timeline, track: str, frame: int, angle_id: str) -> list[Op]:
    """Applique sur `work` (copie) la coupe + le changement d'angle et renvoie les opérations réelles."""
    c = clip_at(work, track, frame)
    if not c.group:
        raise TimelineError(f"{c.id} n'est pas un plan multicam")
    if c.angle == angle_id:
        return []                                  # déjà sur cet angle : on ne coupe pas pour rien
    ops: list[Op] = []
    if frame > c.start:
        rz = razor(work, frame, [track])
        execute(work, rz)
        ops.append(rz)
        c = clip_at(work, track, frame)
    sw = switch_angle(work, c.id, angle_id)
    execute(work, sw)
    ops.append(sw)
    return ops


def cut_to_angle(tl: Timeline, track: str, frame: int, angle_id: str) -> Transaction:
    """Une touche : à `frame`, le plan multicam de `track` passe sur `angle_id` (coupe si besoin)."""
    ops = _cut_ops(tl.copy(), track, frame, angle_id)
    if not ops:
        raise TimelineError(f"le plan est déjà sur l'angle {angle_id} à l'image {frame}")
    return Transaction(ops, f"angle {angle_id} à l'image {frame}")


def record_angle_switches(tl: Timeline, track: str, events: list[tuple[int, str]]) -> Transaction:
    """Une prise multicam : `events` = [(image, angle_id)], dans n'importe quel ordre. Les touches qui ne changent rien
    sont ignorées ; une touche sur un angle inutilisable (non synchronisé) annule toute la prise."""
    if not events:
        raise TimelineError("aucune touche enregistrée")
    work = tl.copy()
    ops: list[Op] = []
    for frame, angle_id in sorted(events, key=lambda e: e[0]):
        ops += _cut_ops(work, track, frame, angle_id)
    if not ops:
        raise TimelineError("la prise ne change aucun angle")
    return Transaction(ops, f"prise multicam sur {track} ({len(events)} touche(s))")
