"""Aperçu image de la timeline (V0.1) : l'image visible sous la tête de lecture, pour N'IMPORTE quel montage (multicam ou non).

Une image = un appel ffmpeg court (`-ss` rapide, 640 px de large, PNG sur la sortie standard). Pas de son à cette étape : la lecture est
un défilement de la tête avec images ; le son se juge à l'export. Ne lit que les rushes (lecture seule). Fonction pure côté données :
`frame_request(tl, frame)` dit quelle image du rush montrer ; `grab_frame` l'extrait."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..ffmpeg import ffmpeg_bin
from ..timeline.flatten import flatten_video


def frame_request(tl, frame: int) -> tuple[str, float] | None:
    """(média, seconde dans le rush) de l'image visible à `frame`, ou None (noir / hors montage)."""
    fl = flatten_video(tl)
    sp = fl.at(frame)
    if sp is None or sp.is_black:
        return None
    return sp.media, round(sp.src_in + (frame - sp.start) / tl.fps * getattr(sp, "speed", 1.0), 6)


def grab_frame(media_root: Path, media: str, t: float, width: int = 640) -> bytes | None:
    cmd = [ffmpeg_bin(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(Path(media_root) / media), "-frames:v", "1",
           "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=20, creationflags=flags).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None
