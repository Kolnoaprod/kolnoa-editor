"""Passerelle entre la liste de décisions (format d'échange avec l'IA) et la timeline.

`from_decision_list` importe un montage existant sans rien perdre ; `to_decision_list` exporte les
montages « simples » (une piste vidéo continue) pour que le rendu actuel et la CLI continuent
de fonctionner. Ce qu'un export ne peut pas représenter est refusé (erreur) ou signalé
(avertissement) : jamais perdu en silence.
"""

from __future__ import annotations

from dataclasses import asdict

from ..decision_list import DecisionList, Music, Segment
from .model import Clip, Timeline, TimelineError, Track
from .timebase import duration_frames, frames_to_seconds

MAIN_TRACK = "V1"


class TimelineExportError(TimelineError):
    """La timeline contient des éléments que la liste de décisions ne sait pas représenter."""


def from_decision_list(dl: DecisionList) -> Timeline:
    clips: list[Clip] = []
    pos = 0
    for seg in dl.segments:
        n = duration_frames(seg.duration, dl.fps)
        clips.append(Clip(id=seg.id, track=MAIN_TRACK, media=seg.clip, start=pos, duration=n,
                          src_in=round(seg.start, 6), audio=seg.audio, fade_in=seg.fade_in,
                          fade_out=seg.fade_out, note=seg.note, parent=seg.parent))
        pos += n
    tl = Timeline(title=dl.title, version=dl.version, fps=dl.fps, resolution=tuple(dl.resolution),
                  source_gain_db=dl.source_gain_db, music=asdict(dl.music) if dl.music else None,
                  tracks=[Track(MAIN_TRACK, "video", "main")], clips=clips, retired=list(dl.retired))
    tl.validate()
    return tl


def to_decision_list(tl: Timeline) -> tuple[DecisionList, list[str]]:
    """Renvoie (liste, avertissements). Lève TimelineExportError si l'export serait infidèle."""
    warnings: list[str] = []
    video = [t for t in tl.tracks if t.kind == "video"]
    if len(video) != 1:
        raise TimelineExportError(f"{len(video)} pistes vidéo : la liste de décisions n'en supporte qu'une")
    main = video[0]
    for c in tl.clips:
        if c.track != main.id:
            raise TimelineExportError(f"{c.id} est sur la piste {c.track} : seules les pistes vidéo "
                                      "principales s'exportent en liste de décisions")
    ordered = tl.clips_on(main.id)
    expected = 0
    for c in ordered:
        if c.start != expected:
            raise TimelineExportError(f"trou avant {c.id} (image {expected} -> {c.start}) : "
                                      "la liste de décisions n'a pas de plans vides")
        expected = c.end
    if main.muted or main.gain_db:
        warnings.append(f"réglages de la piste {main.id} (muet/gain) non exportés")
    segments: list[Segment] = []
    for c in ordered:
        if c.extra:
            warnings.append(f"{c.id}: réglages avancés {sorted(c.extra)} non exportés")
        if c.gain_db:
            warnings.append(f"{c.id}: gain {c.gain_db} dB non exporté")
        end = round(c.src_in + frames_to_seconds(c.duration, tl.fps), 6)
        segments.append(Segment(id=c.id, clip=c.media, start=c.src_in, end=end, audio=c.audio,
                                fade_in=c.fade_in, fade_out=c.fade_out, note=c.note, parent=c.parent))
    if tl.transitions:
        warnings.append(f"{len(tl.transitions)} transition(s) non exportée(s) en liste de décisions "
                        "(rendues à partir de la PR D)")
    if tl.markers:
        warnings.append(f"{len(tl.markers)} repère(s) non exporté(s) (sans effet sur le rendu)")
    dl = DecisionList(title=tl.title, version=tl.version, fps=tl.fps, resolution=tuple(tl.resolution),
                      source_gain_db=tl.source_gain_db, music=Music(**tl.music) if tl.music else None,
                      segments=segments, retired=list(tl.retired))
    return dl, warnings
