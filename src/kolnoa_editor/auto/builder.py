"""Génération du premier montage SUR la timeline de la session (aucun second système) :
un groupe d'annulation `auto`, une transaction par section (progressif : la section en cours seule est « en construction »),
plans posés à la suite sur la piste vidéo, son lié sur la piste audio."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable

from ..timeline import Scope, Session
from ..timeline.chutier import place_rush
from ..timeline.model import Track
from .planner import Shot


@dataclass
class GenerationReport:
    shots: int
    sections: list[str]
    start: int
    end: int
    group_id: str | None


def ensure_tracks(session: Session, video: str, audio: str) -> None:
    have = {t.id for t in session.tl.tracks}
    from ..timeline import AddTrack
    for tid, kind in ((video, "video"), (audio, "audio")):
        if tid not in have:
            session.do(AddTrack(asdict(Track(tid, kind, "main"))), f"piste {tid}")


def generate_first_edit(session: Session, shots: list[Shot], video: str = "V1", audio: str = "A1", at: int | None = None,
                        label: str = "Monte-moi cette séquence", durations: dict[str, float] | None = None,
                        on_section: Callable[[str, int, int], None] | None = None, *, on_file=None, on_file_done=None) -> GenerationReport:
    """Ajoute `shots` à la suite de la piste `video` (à partir de `at`, sinon la fin actuelle). Annulable d'un geste (`undo_group`),
    section par section ; `on_section(nom, début, fin)` permet à l'interface d'afficher le résultat progressivement."""
    ensure_tracks(session, video, audio)
    tl = session.tl
    fps = tl.fps
    pos = at if at is not None else max([c.end for c in tl.clips_on(video)], default=0)
    total = max(1, sum(max(1, round(s.length * fps)) for s in shots))
    first = pos
    sections: list[str] = []
    scope = Scope(pos, pos + total, (video, audio))
    with session.group(label, "auto", scope) as gid:
        cur, sec_start = None, pos
        for s in shots:
            if on_file:
                on_file(s.media)
            if s.section != cur:
                if cur is not None and on_section:
                    on_section(cur, sec_start, pos)
                cur, sec_start = s.section, pos
                sections.append(cur)
            src_len = (durations or {}).get(s.media)
            tx = place_rush(session.tl, s.media, s.length, video, pos, s.src_in, "overwrite", audio, None, "video", "", src_len)
            session.do(tx, f"{s.section} : {s.media.rsplit('/', 1)[-1]} {s.src_in:.1f}s")
            pos += max(1, round(s.length * fps))
            if on_file_done:
                on_file_done(s.media)
        if cur is not None and on_section:
            on_section(cur, sec_start, pos)
    return GenerationReport(len(shots), sections, first, pos, gid if shots else None)
