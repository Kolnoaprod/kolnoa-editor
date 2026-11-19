"""Ciblage explicite des retours réalisateur depuis la tête de lecture.

Ce module ne modifie pas la timeline : il transforme une position ou une plage
en Selection signée, réutilisable par les consignes et les outils manuels.
Les zones vides ne sont jamais élargies silencieusement au film entier.
"""
from __future__ import annotations

from ..timeline.model import Timeline, TimelineError
from ..timeline.selection import Selection


def selection_from_playhead(
    tl: Timeline,
    frame: int,
    *,
    track: str = "V1",
    range_end: int | None = None,
) -> Selection:
    """Cible le plan sous la tête, ou une plage explicitement délimitée.

    Le mode point sélectionne UN plan vidéo sur la piste demandée.
    Une plage conserve ses bornes exactes pour les protections de portée.
    """
    if not isinstance(frame, int) or isinstance(frame, bool) or frame < 0:
        raise TimelineError("position de lecture invalide")
    video_tracks = {t.id for t in tl.tracks if t.kind == "video"}
    if track not in video_tracks:
        raise TimelineError(f"piste vidéo inconnue : {track}")
    if range_end is not None:
        if not isinstance(range_end, int) or isinstance(range_end, bool):
            raise TimelineError("fin de plage invalide")
        selection = Selection.range(tl, frame, range_end, tracks=[track])
        if not selection.clips_in(tl):
            raise TimelineError("aucun plan dans la plage sélectionnée")
        return selection
    candidates = [c for c in tl.clips if c.track == track and c.start <= frame < c.end]
    if not candidates:
        raise TimelineError("aucun plan sous la tête de lecture")
    if len(candidates) > 1:
        raise TimelineError("plusieurs plans sous la tête de lecture : sélectionnez un plan")
    return Selection.of_clips(tl, [candidates[0].id])
