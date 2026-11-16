"""Opérations de montage « à la Premiere / Final Cut » construites sur les opérations réversibles de base.

Tout renvoie une `Transaction` (un seul pas d'annulation, tout ou rien) : l'interface manuelle, la CLI et les
prompts utilisent exactement les mêmes fonctions.

Règles communes :
  - rien ne bouge sur une piste qu'on n'a pas visée, sauf demande explicite (`ripple`, `insert`) ;
  - les plans liés (image + son : même `link`) sont coupés, déplacés et supprimés ensemble, sauf `linked=False` ;
  - une piste verrouillée ou un plan verrouillé refuse la modification (le tout est alors annulé) ;
  - le ripple refuse de désynchroniser une piste verrouillée qui porte des plans après le point d'édition.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from .model import Clip, LockedError, Timeline, TimelineError
from .ops import (AddClip, MoveClip, Op, RemoveClip, SetClipProps, ShiftAfter, ShiftClips, SplitClip, Transaction, TrimClip)

MODES = ("overwrite", "insert")


class _Ids:
    """Ids de plans neufs, jamais déjà utilisés ni donnés deux fois dans la même transaction."""

    def __init__(self, tl: Timeline):
        self.tl, self.taken = tl, set(tl._used_clip_ids())
        self.links = {c.link for c in tl.clips if c.link}

    def clip(self) -> str:
        n = 1
        while f"s{n:03d}" in self.taken:
            n += 1
        cid = f"s{n:03d}"
        self.taken.add(cid)
        return cid

    def link(self) -> str:
        n = 1
        while f"l{n:03d}" in self.links:
            n += 1
        lid = f"l{n:03d}"
        self.links.add(lid)
        return lid


# --- liens ------------------------------------------------------------------------------------
def linked_ids(tl: Timeline, clip_id: str) -> list[str]:
    """Le plan et ses partenaires liés (ordre de la timeline)."""
    c = tl.clip(clip_id)
    if not c.link:
        return [c.id]
    return [x.id for x in tl.clips if x.link == c.link]


def link_clips(tl: Timeline, clip_ids: list[str]) -> Transaction:
    if len(set(clip_ids)) < 2:
        raise TimelineError("lier demande au moins deux plans")
    clips = [tl.clip(i) for i in clip_ids]
    if len({c.track for c in clips}) < len(clips):
        raise TimelineError("deux plans liés ne peuvent pas être sur la même piste")
    lid = _Ids(tl).link()
    return Transaction([SetClipProps(c.id, {"link": lid}) for c in clips], f"liés : {', '.join(clip_ids)}")


def unlink_clips(tl: Timeline, clip_id: str) -> Transaction:
    ids = linked_ids(tl, clip_id)
    return Transaction([SetClipProps(i, {"link": None}) for i in ids], f"déliés : {', '.join(ids)}")


# --- outils de coupe ---------------------------------------------------------------------------
def razor(tl: Timeline, frame: int, tracks: list[str] | None = None, linked: bool = True) -> Transaction:
    """Lame de rasoir : coupe à `frame` les plans qui la contiennent strictement.

    `tracks=None` : toutes les pistes déverrouillées (les pistes verrouillées sont épargnées, comme dans Premiere).
    Piste nommée explicitement et verrouillée : refus. Les plans liés sont coupés ensemble et leurs moitiés droites
    restent liées entre elles."""
    ids = _Ids(tl)
    targets: list[Clip] = []
    for t in tl.tracks:
        if tracks is not None and t.id not in tracks:
            continue
        if t.locked:
            if tracks is not None:
                raise LockedError(f"la piste {t.id} est verrouillée")
            continue
        targets += [c for c in tl.clips_on(t.id) if c.start < frame < c.end]
    if linked:
        have = {c.id for c in targets}
        for c in list(targets):
            for pid in linked_ids(tl, c.id):
                p = tl.clip(pid)
                if pid not in have and p.start < frame < p.end:
                    if tl.track(p.track).locked:
                        raise LockedError(f"{p.id} (lié à {c.id}) est sur la piste verrouillée {p.track}")
                    targets.append(p)
                    have.add(pid)
    if not targets:
        raise TimelineError(f"rien à couper à l'image {frame}")
    new_link: dict[str, str] = {}
    ops: list[Op] = []
    for c in targets:
        nid = ids.clip()
        ops.append(SplitClip(c.id, frame, nid))
        if c.link:
            lid = new_link.setdefault(c.link, ids.link())
            ops.append(SetClipProps(nid, {"link": lid}))
    return Transaction(ops, f"rasoir à l'image {frame} ({len(targets)} plan(s))")


def _clear_span(tl: Timeline, track: str, start: int, end: int, exclude: set[str], ids: _Ids) -> list[Op]:
    """Opérations qui vident [start, end) sur une piste (recouvrement). Les bords coupés n'ont pas de fondu."""
    ops: list[Op] = []
    for e in tl.clips_on(track):
        if e.id in exclude or e.end <= start or e.start >= end:
            continue
        if e.start >= start and e.end <= end:
            ops.append(RemoveClip(e.id))
        elif e.start < start and e.end > end:                       # le nouveau plan tombe au milieu de e
            nid = ids.clip()
            ops.append(SplitClip(e.id, start, nid))
            ops.append(SetClipProps(nid, {"link": None}))
            ops.append(TrimClip(nid, end, e.end - end, round(e.src_in + (end - e.start) / tl.fps, 6)))
            ops.append(SetClipProps(nid, {"fade_in": 0.0}))
        elif e.start < start:                                       # e déborde à gauche : on raccourcit sa fin
            ops.append(SetClipProps(e.id, {"fade_out": 0.0}))
            ops.append(TrimClip(e.id, e.start, start - e.start, e.src_in))
        else:                                                       # e déborde à droite : on raccourcit son début
            ops.append(SetClipProps(e.id, {"fade_in": 0.0}))
            ops.append(TrimClip(e.id, end, e.end - end, round(e.src_in + (end - e.start) / tl.fps, 6)))
    return ops


def _make_room_insert(tl: Timeline, tracks: list[str], at: int, length: int, exclude: set[str], ids: _Ids) -> list[Op]:
    ops: list[Op] = []
    shift: list[str] = []
    for tid in tracks:
        for c in tl.clips_on(tid):
            if c.id in exclude:
                continue
            if c.start < at < c.end:
                nid = ids.clip()
                ops.append(SplitClip(c.id, at, nid))
                ops.append(SetClipProps(nid, {"link": None}))
                shift.append(nid)
            elif c.start >= at:
                shift.append(c.id)
    if shift:
        ops.append(ShiftClips(shift, length))
    return ops


def place_clip(tl: Timeline, clip: Clip | dict, mode: str = "overwrite", ripple_tracks: list[str] | None = None) -> Transaction:
    """Pose un nouveau plan. `overwrite` : il recouvre ce qui se trouve dessous (rien d'autre ne bouge).
    `insert` : la suite est décalée vers la droite sur la piste visée (et sur `ripple_tracks` en plus)."""
    c = Clip(**clip) if isinstance(clip, dict) else clip
    if mode not in MODES:
        raise TimelineError(f"mode inconnu : {mode!r} (valeurs : {', '.join(MODES)})")
    tl.track(c.track)
    ids = _Ids(tl)
    if mode == "overwrite":
        ops = _clear_span(tl, c.track, c.start, c.end, set(), ids)
    else:
        tracks = list(dict.fromkeys([c.track, *(ripple_tracks or [])]))
        _refuse_locked_desync(tl, [t for t in tl.tracks if t.id not in tracks], c.start)
        ops = _make_room_insert(tl, tracks, c.start, c.duration, set(), ids)
    ops.append(AddClip(asdict(c)))
    return Transaction(ops, f"{'recouvre' if mode == 'overwrite' else 'insère'} {c.id} ({c.duration} i à l'image {c.start})")


def _refuse_locked_desync(tl: Timeline, other_tracks, at: int) -> None:
    for t in other_tracks:
        if t.locked and any(c.end > at for c in tl.clips_on(t.id)):
            raise LockedError(f"la piste verrouillée {t.id} porte des plans après l'image {at} : "
                              "le décalage la désynchroniserait (déverrouiller ou viser les pistes à la main)")


def move_clips(tl: Timeline, clip_id: str, start: int, track: str | None = None, linked: bool = True,
               mode: str = "refuse") -> Transaction:
    """Déplace un plan (et ses liés, du même nombre d'images ; seul le plan visé change de piste).
    `refuse` (défaut) : erreur si le résultat chevauche un plan ; `overwrite` : recouvre ce qui se trouve dessous.
    Rien d'autre ne bouge : les autres plans de la piste d'origine restent en place."""
    if mode not in ("refuse", "overwrite"):
        raise TimelineError(f"mode inconnu : {mode!r}")
    c = tl.clip(clip_id)
    delta = start - c.start
    moving = linked_ids(tl, clip_id) if linked else [clip_id]
    ids = _Ids(tl)
    ops: list[Op] = []
    if mode == "overwrite":
        exclude = set(moving)
        for mid in moving:
            m = tl.clip(mid)
            dest_track = (track if (mid == clip_id and track) else m.track)
            ops += _clear_span(tl, dest_track, m.start + delta, m.end + delta, exclude, ids)
    for mid in moving:
        m = tl.clip(mid)
        ops.append(MoveClip(mid, m.start + delta, track if (mid == clip_id and track) else None))
    return Transaction(ops, f"{clip_id} déplacé à l'image {start}" + (f" sur {track}" if track else ""))


def replace_clip(tl: Timeline, target_id: str, media: str, src_in: float) -> Transaction:
    """Remplacement : le plan garde sa place et sa durée ; seule la source change. Refusé pour un plan multicam
    (changer d'angle : switch_angle)."""
    c = tl.clip(target_id)
    if c.group:
        raise TimelineError(f"{c.id} est un plan multicam : changer d'angle avec switch_angle")
    if src_in < 0:
        raise TimelineError("point d'entrée source négatif")
    return Transaction([TrimClip(c.id, c.start, c.duration, src_in), SetClipProps(c.id, {"media": media})],
                       f"{c.id} remplacé par {media}")


def delete_range(tl: Timeline, start: int, end: int, tracks: list[str] | None = None, ripple: bool = False,
                 linked: bool = True) -> Transaction:
    """Retire la portion [start, end) des pistes visées (toutes les pistes déverrouillées par défaut).
    Les pistes portant des plans liés à ceux de la plage sont ajoutées (`linked`). `ripple` referme le trou :
    tout ce qui suit est avancé de `end - start` sur ces mêmes pistes, donc le montage reste synchrone."""
    if not (0 <= start < end):
        raise TimelineError(f"plage invalide : {start}-{end}")
    length = end - start
    ids = _Ids(tl)
    wanted = [t.id for t in tl.tracks] if tracks is None else list(tracks)
    for tid in wanted:
        if tl.track(tid).locked and tracks is not None:
            raise LockedError(f"la piste {tid} est verrouillée")
    wanted = [tid for tid in wanted if not tl.track(tid).locked]
    if linked and tracks is not None:
        for tid in list(wanted):
            for c in tl.clips_on(tid):
                if c.end > start and c.start < end:
                    for pid in linked_ids(tl, c.id):
                        pt = tl.clip(pid).track
                        if pt not in wanted:
                            if tl.track(pt).locked:
                                raise LockedError(f"{pid} (lié à {c.id}) est sur la piste verrouillée {pt}")
                            wanted.append(pt)
    if ripple:
        _refuse_locked_desync(tl, [t for t in tl.tracks if t.id not in wanted], start)
    ops: list[Op] = []
    for tid in wanted:
        ops += _clear_span(tl, tid, start, end, set(), ids)
    if ripple:
        ops.append(ShiftAfter(wanted, end, -length))
    if not ops:
        raise TimelineError("rien à retirer dans cette plage")
    return Transaction(ops, f"retire {length} i ({start}-{end}){' avec ripple' if ripple else ''}")


# --- ajustement des bords -----------------------------------------------------------------------
def ripple_trim(tl: Timeline, clip_id: str, edge: str, delta: int, linked: bool = True,
                source_len: Callable[[str], float | None] | None = None) -> Transaction:
    """Rogne un bord (`head` ou `tail`) de `delta` images (> 0 raccourcit, < 0 rallonge) et décale la suite de la
    piste pour garder le montage continu. Les plans liés sont rognés pareil (et leur suite décalée)."""
    if edge not in ("head", "tail") or delta == 0:
        raise TimelineError("bord 'head' ou 'tail' et delta non nul requis")
    ops: list[Op] = []
    for cid in (linked_ids(tl, clip_id) if linked else [clip_id]):
        c = tl.clip(cid)
        new_src = c.src_in if edge == "tail" else round(c.src_in + delta / tl.fps, 6)
        _check_source(tl, c, new_src, c.duration - delta, source_len)
        ops.append(TrimClip(c.id, c.start, c.duration - delta, new_src))
        if _has_after(tl, c):
            ops.append(ShiftAfter([c.track], c.end, -delta))
    return Transaction(ops, f"{clip_id} : bord {edge} {delta:+d} i (ripple)")


def _has_after(tl: Timeline, c: Clip) -> bool:
    return any(x.start >= c.end for x in tl.clips_on(c.track))


def _check_source(tl: Timeline, c: Clip, src_in: float, duration: int, source_len) -> None:
    if src_in < -1e-9:
        raise TimelineError(f"{c.id}: pas assez de matière avant le plan (poignée épuisée)")
    if source_len is not None and not c.group:
        total = source_len(c.media)
        if total is not None and src_in + duration / tl.fps > total + 1e-6:
            raise TimelineError(f"{c.id}: pas assez de matière après le plan (le rush se termine à {total:.2f} s)")


def roll_trim(tl: Timeline, left_id: str, right_id: str, delta: int,
              source_len: Callable[[str], float | None] | None = None) -> Transaction:
    """Déplace la coupe entre deux plans contigus d'une même piste : `delta` > 0 la recule (le plan de gauche
    s'allonge, celui de droite raccourcit), `delta` < 0 l'avance. C'est la « compensation des plans voisins » :
    la durée totale et tout le reste du montage ne bougent pas."""
    l, r = tl.clip(left_id), tl.clip(right_id)
    if l.track != r.track or l.end != r.start:
        raise TimelineError(f"{l.id} et {r.id} ne sont pas contigus sur la même piste")
    if delta == 0:
        raise TimelineError("delta nul")
    if l.duration + delta <= 0 or r.duration - delta <= 0:
        raise TimelineError("la coupe ne peut pas sortir des deux plans")
    new_src = round(r.src_in + delta / tl.fps, 6)
    _check_source(tl, l, l.src_in, l.duration + delta, source_len)
    _check_source(tl, r, new_src, r.duration - delta, source_len)
    return Transaction([TrimClip(l.id, l.start, l.duration + delta, l.src_in),
                        TrimClip(r.id, r.start + delta, r.duration - delta, new_src)],
                       f"coupe {l.id}|{r.id} déplacée de {delta:+d} i")


# --- aimantation (magnétisme) ---------------------------------------------------------------------
def snap_points(tl: Timeline, exclude: set[str] | None = None, playhead: int | None = None,
                tracks: list[str] | None = None) -> list[int]:
    """Images « aimantées » : bords des plans, repères, tête de lecture, début du film."""
    pts = {0}
    for c in tl.clips:
        if (exclude and c.id in exclude) or (tracks is not None and c.track not in tracks):
            continue
        pts.update((c.start, c.end))
    pts.update(m.frame for m in tl.markers)
    if playhead is not None:
        pts.add(playhead)
    return sorted(pts)


def snap_frame(frame: int, points: list[int], threshold: int) -> int:
    """Point le plus proche à moins de `threshold` images, sinon `frame` inchangé (à égalité : le plus à gauche)."""
    best = None
    for p in points:
        d = abs(p - frame)
        if d <= threshold and (best is None or d < abs(best - frame)):
            best = p
    return frame if best is None else best


def snap_move(tl: Timeline, clip_id: str, start: int, threshold: int, playhead: int | None = None) -> int:
    """Début proposé pour un plan qu'on fait glisser : aimante son début OU sa fin sur le point le plus proche
    (jamais sur ses propres bords ni sur ceux de ses liés, qui bougent avec lui)."""
    c = tl.clip(clip_id)
    pts = snap_points(tl, exclude=set(linked_ids(tl, clip_id)), playhead=playhead)
    a = snap_frame(start, pts, threshold)
    b = snap_frame(start + c.duration, pts, threshold) - c.duration
    moved = [(abs(x - start), i, x) for i, x in enumerate((a, b)) if x != start]
    return min(moved)[2] if moved else start
