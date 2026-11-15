"""Rendu d'une liste de décisions en MP4 avec ffmpeg.

Principe : chaque segment est encodé séparément (mêmes paramètres pour tous), puis concaténé
sans ré-encodage ; la musique est mixée dans une dernière passe (la vidéo est copiée).
'proxy' = rapide, à partir des proxys, pour la revue ; 'final' = à partir des originaux.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable

from .decision_list import DecisionList, Segment
from .ffmpeg import ffmpeg_bin, run, video_encoder_args
from .probe import probe
from .project import Project

SAMPLE_RATE = 48000


def _source_for(project: Project, seg: Segment, kind: str) -> Path:
    original = project.resolve(seg.clip)
    if kind == "proxy":
        proxy = project.proxy_path(seg.clip)
        if proxy.is_file():
            return proxy
    return original


def _video_filter(dl: DecisionList, seg: Segment, kind: str) -> str:
    w, h = dl.resolution
    if kind == "proxy":
        w, h = (w // 2) // 2 * 2, (h // 2) // 2 * 2
    chain = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        f"fps={dl.fps}",
        "setsar=1",
        "format=yuv420p",
    ]
    if seg.fade_in > 0:
        chain.append(f"fade=t=in:st=0:d={seg.fade_in}")
    if seg.fade_out > 0:
        chain.append(f"fade=t=out:st={seg.duration - seg.fade_out}:d={seg.fade_out}")
    return ",".join(chain)


def _audio_filter(dl: DecisionList, seg: Segment, muted: bool) -> str:
    chain = [f"aresample={SAMPLE_RATE}"]
    if muted:
        chain.append("volume=0")
    elif dl.source_gain_db:
        chain.append(f"volume={dl.source_gain_db}dB")
    if seg.fade_in > 0:
        chain.append(f"afade=t=in:st=0:d={seg.fade_in}")
    if seg.fade_out > 0:
        chain.append(f"afade=t=out:st={seg.duration - seg.fade_out}:d={seg.fade_out}")
    return ",".join(chain)


def _encode_segment(project: Project, dl: DecisionList, seg: Segment, kind: str, out: Path) -> None:
    src = _source_for(project, seg, kind)
    dur = seg.duration
    has_audio = probe(src).has_audio
    use_source_audio = has_audio and seg.audio == "source"
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-v", "error",
           "-ss", f"{seg.start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    if has_audio:
        audio_map = ["-map", "0:a:0"]
    else:  # drone sans son : piste silencieuse pour garder des segments homogènes
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
        audio_map = ["-map", "1:a:0"]
    cmd += [
        "-map", "0:v:0", *audio_map,
        "-vf", _video_filter(dl, seg, kind),
        "-af", _audio_filter(dl, seg, muted=not use_source_audio),
        *video_encoder_args("proxy" if kind == "proxy" else "final"),
        "-r", str(dl.fps), "-g", str(dl.fps * 2),
        "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-t", f"{dur:.3f}",
        str(out),
    ]
    run(cmd)


def render(
    project: Project,
    dl: DecisionList,
    kind: str = "proxy",
    out: Path | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    if kind not in ("proxy", "final"):
        raise ValueError("kind doit être 'proxy' ou 'final'")
    dl.validate(project)
    if not dl.segments:
        raise ValueError("Rien à rendre : aucun segment")
    project.ensure()
    out = out or project.render_path(dl.version, kind)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="kolnoa_") as tmp_name:
        tmp = Path(tmp_name)
        parts: list[Path] = []
        for i, seg in enumerate(dl.segments, 1):
            if on_progress:
                on_progress(i, len(dl.segments), seg.id)
            part = tmp / f"{i:04d}.mp4"
            _encode_segment(project, dl, seg, kind, part)
            parts.append(part)

        list_file = tmp / "list.txt"
        list_file.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8"
        )
        joined = tmp / "joined.mp4"
        run([ffmpeg_bin(), "-y", "-hide_banner", "-v", "error",
             "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(joined)])

        if dl.music is None:
            shutil.move(str(joined), out)
            return out

        total = dl.duration
        music = project.resolve(dl.music.path)
        fade_start = max(0.0, total - dl.music.fade_out)
        filt = (
            f"[1:a]atrim=start={dl.music.start},asetpts=PTS-STARTPTS,"
            f"volume={dl.music.gain_db}dB,afade=t=out:st={fade_start}:d={dl.music.fade_out}[m];"
            f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
        run([ffmpeg_bin(), "-y", "-hide_banner", "-v", "error",
             "-i", str(joined), "-i", str(music),
             "-filter_complex", filt, "-map", "0:v:0", "-map", "[a]",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
             str(out)])
    return out
