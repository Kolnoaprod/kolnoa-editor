"""Rendu d'une liste de décisions en MP4 avec ffmpeg.

Principe : chaque segment est encodé séparément (mêmes paramètres pour tous) dans un cache, en
vidéo H.264 avec un nombre d'images EXACT et en audio PCM de durée exacte ; les segments sont
concaténés sans ré-encodage vidéo, puis l'audio (son direct + musique) est encodé en AAC une seule
fois. Ainsi aucun trou ni clic aux raccords (le décalage de début de l'AAC, ~20 ms par segment,
créait des trous d'une image et une cadence variable) et la cadence reste constante. 'proxy' = rapide, à partir des proxys, pour la revue ; 'final' = à partir des
originaux.

Fiabilité :
- cache de segments : une clé (source, plage, fondus, gain, cadence, résolution, encodeur) identifie
  chaque segment encodé. Un rendu interrompu reprend où il s'était arrêté ; une modification de
  quelques plans ne ré-encode que ces plans ;
- tout est écrit dans des fichiers .part, renommés à la fin : un échec ou une annulation ne détruit
  jamais le rendu précédent ni ne laisse un fichier tronqué sous le nom final ;
- la durée du fichier produit est vérifiée ;
- annulation (CancelToken / Ctrl+C), progression, journal (<données>/logs/kolnoa.log).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Callable

from .decision_list import DecisionList, Segment
from .ffmpeg import (CancelToken, Cancelled, FFmpegError, encoder_name, ffmpeg_bin, run,
                     video_encoder_args)
from .preflight import PreflightError, has_errors, preflight
from .probe import probe, probe_cached
from .project import Project
from .proxies import proxy_state

log = logging.getLogger("kolnoa.render")

SAMPLE_RATE = 48000
CACHE_VERSION = 2


class RenderError(RuntimeError):
    pass


def _source_for(project: Project, seg: Segment, kind: str) -> Path:
    original = project.resolve(seg.clip)
    if kind == "proxy":
        proxy = project.proxy_path(seg.clip)
        if proxy_state(original, proxy) == "ok":  # jamais un proxy périmé
            return proxy
    return original


def _video_filter(dl: DecisionList, seg: Segment, kind: str) -> str:
    w, h = dl.resolution
    if kind == "proxy":
        w, h = (w // 2) // 2 * 2, (h // 2) // 2 * 2
    chain = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        f"fps={dl.fps}:start_time=0",
        "tpad=stop_mode=clone:stop_duration=1",
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


def segment_cache_key(dl: DecisionList, seg: Segment, kind: str, src: Path) -> str:
    st = src.stat()
    payload = json.dumps(
        [CACHE_VERSION, kind, seg.clip, str(src.name), st.st_size, st.st_mtime_ns,
         round(seg.start, 3), round(seg.end, 3), seg.audio, seg.fade_in, seg.fade_out,
         dl.source_gain_db, dl.fps, list(dl.resolution), encoder_name()],
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def segment_frames(dl: DecisionList, seg: Segment) -> int:
    return max(1, round(seg.duration * dl.fps))


def timeline_duration(dl: DecisionList) -> float:
    """Durée réelle du film rendu : somme des segments arrondis à l'image."""
    return sum(segment_frames(dl, s) for s in dl.segments) / dl.fps


def _encode_segment(project: Project, dl: DecisionList, seg: Segment, kind: str, src: Path,
                    out: Path, cancel: CancelToken | None) -> None:
    frames = segment_frames(dl, seg)
    dur = frames / dl.fps          # durée exacte : N images, N*48000/fps échantillons
    has_audio = probe_cached(src).has_audio
    use_source_audio = has_audio and seg.audio == "source"
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-v", "error",
           "-ss", f"{seg.start:.3f}", "-i", str(src)]
    if has_audio:
        audio_map = ["-map", "0:a:0"]
    else:  # drone sans son : piste silencieuse pour garder des segments homogènes
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
        audio_map = ["-map", "1:a:0"]
    cmd += [
        "-map", "0:v:0", *audio_map,
        "-vf", _video_filter(dl, seg, kind),
        "-af", _audio_filter(dl, seg, muted=not use_source_audio) + f",apad=whole_dur={dur:.6f}",
        *video_encoder_args("proxy" if kind == "proxy" else "final"),
        "-r", str(dl.fps), "-g", str(dl.fps * 2),
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-frames:v", str(frames), "-t", f"{dur:.6f}",
        str(out),
    ]
    run(cmd, cancel)


def _concat_line(path: Path) -> str:
    """Ligne du fichier de concaténation ffmpeg ; les apostrophes sont échappées ('\\'')."""
    return "file '" + path.resolve().as_posix().replace("'", "'\\''") + "'\n"


def clean_cache(project: Project) -> int:
    """Vide le cache de segments (uniquement dans le dossier de données). Retourne les Mo libérés."""
    seg_dir = project.cache_dir / "segments"
    if not seg_dir.exists():
        return 0
    if not project.is_in_data_dir(seg_dir):
        raise RenderError(f"Refus de nettoyer un dossier hors des données Kolnoa : {seg_dir}")
    freed = 0
    for p in seg_dir.iterdir():
        if p.is_file():
            freed += p.stat().st_size
            p.unlink()
    return freed // 1_000_000


def render(
    project: Project,
    dl: DecisionList,
    kind: str = "proxy",
    out: Path | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    cancel: CancelToken | None = None,
) -> Path:
    if kind not in ("proxy", "final"):
        raise ValueError("kind doit être 'proxy' ou 'final'")
    out = out or project.render_path(dl.version, kind)

    issues = preflight(project, dl, kind, out_dir=out.parent)
    for issue in issues:
        log.log(logging.ERROR if issue.level == "error" else logging.WARNING
                if issue.level == "warning" else logging.INFO, "preflight %s", issue)
    if has_errors(issues):
        raise PreflightError(issues)

    project.ensure()
    out.parent.mkdir(parents=True, exist_ok=True)
    seg_dir = project.cache_dir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    part = out.with_name(f"{out.stem}.{token}.part.mp4")
    log.info("rendu %s v%03d (%d segments, %.1fs) -> %s", kind, dl.version, len(dl.segments),
             dl.duration, out)
    try:
        cached: list[Path] = []
        encoded = reused = 0
        for i, seg in enumerate(dl.segments, 1):
            if cancel is not None:
                cancel.raise_if_cancelled()
            if on_progress:
                on_progress(i, len(dl.segments), seg.id)
            src = _source_for(project, seg, kind)
            target = seg_dir / f"{segment_cache_key(dl, seg, kind, src)}.mkv"
            if target.is_file() and target.stat().st_size > 0:
                reused += 1
            else:
                tmp = target.with_name(target.stem + f".{token}.part.mkv")
                try:
                    _encode_segment(project, dl, seg, kind, src, tmp, cancel)
                    os.replace(tmp, target)
                finally:
                    tmp.unlink(missing_ok=True)
                encoded += 1
            cached.append(target)
        log.info("segments : %d encodés, %d réutilisés du cache", encoded, reused)

        if on_progress:
            on_progress(len(dl.segments), len(dl.segments), "assemblage")
        total = timeline_duration(dl)
        list_file = project.tmp_dir / f"concat_{token}.txt"
        list_file.write_text("".join(_concat_line(p) for p in cached), encoding="utf-8")
        try:
            cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-v", "error",
                   "-f", "concat", "-safe", "0", "-i", str(list_file)]
            if dl.music is None:
                cmd += ["-map", "0:v:0", "-map", "0:a:0"]
            else:
                music = project.resolve(dl.music.path)
                fade_start = max(0.0, total - dl.music.fade_out)
                filt = (
                    f"[1:a]atrim=start={dl.music.start},asetpts=PTS-STARTPTS,"
                    f"volume={dl.music.gain_db}dB,afade=t=out:st={fade_start}:d={dl.music.fade_out}[m];"
                    f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
                )
                cmd += ["-i", str(music), "-filter_complex", filt, "-map", "0:v:0", "-map", "[a]"]
            cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
                    "-t", f"{total:.6f}", "-movflags", "+faststart", str(part)]
            run(cmd, cancel)
        finally:
            list_file.unlink(missing_ok=True)

        # vérification : un rendu de durée inattendue n'est jamais publié
        actual = probe(part).duration
        if abs(actual - total) > 0.15:
            raise RenderError(
                f"Durée inattendue : {actual:.2f}s au lieu de {total:.2f}s"
            )
        os.replace(part, out)
        log.info("rendu terminé : %s (%.2fs)", out, actual)
        return out
    except Cancelled:
        log.warning("rendu annulé ; les segments déjà encodés restent en cache pour la reprise")
        raise
    except (FFmpegError, RenderError):
        log.exception("rendu échoué")
        raise
    finally:
        part.unlink(missing_ok=True)
