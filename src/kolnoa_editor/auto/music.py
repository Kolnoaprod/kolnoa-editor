"""Musique du montage : un plan audio ordinaire sur une piste dédiée (`A2`, rôle musique), donc mixé par le même moteur que le reste,
visible et modifiable à la main, annulable. Aucune « musique magique » : le réalisateur choisit le fichier ; le logiciel règle le niveau,
la durée (coupée à la fin du film) et le fondu de sortie.

Le niveau par défaut (−14 dB) est un point de départ, pas un réglage artistique : à ajuster à l'oreille."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ..timeline import AddTrack, Clip, Scope, Session, TimelineError
from ..timeline.model import Track
from ..timeline.ops import AddClip


def place_music(session: Session, media: str, music_len_s: float, gain_db: float = -14.0, fade_out_s: float = 3.0,
                track: str = "A2", start: int = 0) -> str:
    """Pose la musique de `start` jusqu'à la fin du montage (ou la fin du fichier si plus court). Renvoie l'id du plan."""
    tl = session.tl
    end_film = max([c.end for c in tl.clips if tl.track(c.track).kind == "video"], default=0)
    if end_film <= start:
        raise TimelineError("rien à accompagner : montez d'abord la séquence")
    frames = min(end_film - start, int(music_len_s * tl.fps))
    if frames < 1:
        raise TimelineError("fichier musical vide ou trop court")
    if any(c.track == track and c.start < start + frames and c.end > start for c in tl.clips):
        raise TimelineError(f"la piste {track} contient déjà un plan sur cette zone : retirez-le ou choisissez une autre piste")
    with session.group("musique", "directive", Scope(start, start + frames, (track,))):
        if track not in {t.id for t in tl.tracks}:
            session.do(AddTrack(asdict(Track(track, "audio", "musique"))), f"piste {track}")
        cid = tl.next_clip_id()
        clip = Clip(id=cid, track=track, media=media, start=start, duration=frames, src_in=0.0, audio="source", gain_db=gain_db,
                    fade_out=min(fade_out_s, frames / tl.fps), label="musique")
        session.do(AddClip(asdict(clip)), "musique")
    return cid
