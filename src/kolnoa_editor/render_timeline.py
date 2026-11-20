"""Export MP4 d'une TIMELINE (J1-b) : plusieurs pistes vidéo et audio, trous, plans de coupe, multicam.

Chaîne : `flatten_video` (la piste visible la plus haute gagne ; trou = noir explicite) → un segment vidéo par tronçon, encodé avec un
nombre d'images EXACT dans un cache (clé = source, plage, cadence, résolution, encodeur ; reprise après interruption) → concaténation
sans ré-encodage → mixage audio de TOUTES les pistes (`render_mix`, WAV 24 bits) → assemblage AAC. Fichiers `.part` renommés à la fin ;
la durée du fichier produit est vérifiée. Les rushes originaux ne sont jamais écrits.

Pas encore pris en charge : transitions/fondus vidéo, étalonnage, vitesse variable avec audio maître multicam."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .ffmpeg import CancelToken, Cancelled, FFmpegError, encoder_name, ffmpeg_bin, run, video_encoder_args
from .probe import probe
from .timeline import Timeline
from .timeline.audio_mix import render_mix
from .timeline.flatten import Flattened, VideoSpan, check_flatten, flatten_video

log = logging.getLogger("kolnoa.render")
CACHE_VERSION = 2
SAMPLE_RATE = 48000


PRESETS: dict[str, tuple[int, int]] = {"4k": (3840, 2160), "1080p": (1920, 1080)}


def resolve_resolution(tl: Timeline, resolution: "str | tuple[int, int] | None") -> tuple[int, int]:
    """Résolution de SORTIE : préréglage (`4k`, `1080p`), couple (largeur, hauteur) ou, par défaut, celle de la timeline.
    Indépendante des sources : un rush 1080p dans un export 4K est mis à l'échelle (et inversement) avec bandes si le ratio diffère."""
    if resolution is None:
        return tuple(tl.resolution)
    if isinstance(resolution, str):
        key = resolution.lower().replace(" ", "")
        if key not in PRESETS:
            raise ValueError(f"format inconnu : {resolution!r} (choix : {', '.join(PRESETS)})")
        return PRESETS[key]
    w, h = resolution
    if w < 16 or h < 16 or w % 2 or h % 2:
        raise ValueError("résolution invalide (dimensions paires, ≥ 16)")
    return (int(w), int(h))


class TimelineRenderError(RuntimeError):
    def __init__(self, message: str, issues: list | None = None):
        super().__init__(message)
        self.issues = issues or []


@dataclass
class RenderReport:
    output: Path
    seconds: float
    frames: int
    segments: int
    encoded: int
    reused: int
    resolution: tuple[int, int]
    warnings: list[str] = field(default_factory=list)


def _size(tl: Timeline, kind: str, res: tuple[int, int] | None = None) -> tuple[int, int]:
    w, h = res or tl.resolution
    if kind == "proxy":
        w, h = (w // 2) // 2 * 2, (h // 2) // 2 * 2
    return w, h


def _span_key(span: VideoSpan, src: Path | None, tl: Timeline, kind: str, res: tuple[int, int]) -> str:
    st = src.stat() if src else None
    payload = json.dumps([CACHE_VERSION, kind, span.media, st.st_size if st else 0, st.st_mtime_ns if st else 0,
                          round(span.src_in, 6), span.speed, span.end - span.start, tl.fps, list(_size(tl, kind, res)), encoder_name()])
    return hashlib.sha1(payload.encode()).hexdigest()[:24]


def _encode_span(tl: Timeline, span: VideoSpan, src: Path | None, kind: str, res: tuple[int, int], out: Path,
                 cancel: CancelToken | None) -> None:
    w, h = _size(tl, kind, res)
    frames = span.end - span.start
    if src is None:
        cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={tl.fps}"]
        vf = "setsar=1,format=yuv420p"
    else:
        cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", "-ss", f"{span.src_in:.6f}", "-i", str(src)]
        vf = (f"setpts=(PTS-STARTPTS)/{span.speed:.8f},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={tl.fps}:start_time=0,"
              "tpad=stop_mode=clone:stop_duration=1,setsar=1,format=yuv420p")
    cmd += ["-map", "0:v:0", "-an", "-vf", vf, *video_encoder_args("proxy" if kind == "proxy" else "final"),
            "-r", str(tl.fps), "-g", str(tl.fps * 2), "-frames:v", str(frames), str(out)]
    run(cmd, cancel)


def preflight_timeline(tl: Timeline, media_root: Path, *, cancel=None) -> tuple[Flattened, list]:
    fl = flatten_video(tl)

    def exists(m): return (media_root / m).is_file()

    def length(m):
        try:
            return probe(media_root / m, cancel=cancel).duration
        except Cancelled:
            raise
        except Exception:                                    # noqa: BLE001 — illisible : signalé par `exists`/rendu
            return None

    issues = check_flatten(tl, fl, exists, length)
    for c in tl.clips:
        if tl.track(c.track).kind == "video" and (c.fade_in or c.fade_out):
            from .timeline.flatten import Issue
            issues.append(Issue("warning", f"{c.id}: fondus vidéo non rendus dans cette version (le son garde ses fondus)"))
    if tl.crossfades:
        from .timeline.flatten import Issue
        issues.append(Issue("warning", "transitions : non rendues dans cette version"))
    return fl, issues


def render_timeline(tl: Timeline, media_root: Path | str, out: Path | str, cache_dir: Path | str, kind: str = "final",
                    on_progress: Callable[[int, int, str], None] | None = None, cancel: CancelToken | None = None,
                    resolution: "str | tuple[int, int] | None" = None) -> RenderReport:
    if kind not in ("proxy", "final"):
        raise ValueError("kind doit être 'proxy' ou 'final'")
    res = resolve_resolution(tl, resolution)
    media_root, out, cache = Path(media_root), Path(out), Path(cache_dir)
    fl, issues = preflight_timeline(tl, media_root, cancel=cancel)
    errors = [i for i in issues if i.level == "error"]
    if errors:
        raise TimelineRenderError("export impossible : " + " ; ".join(i.message for i in errors[:4]), issues)
    if fl.total <= 0:
        raise TimelineRenderError("la timeline est vide", issues)
    seg_dir = cache / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    part = out.with_name(f"{out.stem}.{token}.part.mp4")
    wav = cache / f"mix_{token}.wav"
    concat = cache / f"concat_{token}.txt"
    cached, encoded, reused = [], 0, 0
    try:
        for i, sp in enumerate(fl.spans, 1):
            if cancel is not None:
                cancel.raise_if_cancelled()
            if on_progress:
                on_progress(i, len(fl.spans), sp.media or "noir")
            src = (media_root / sp.media) if sp.media else None
            target = seg_dir / f"{_span_key(sp, src, tl, kind, res)}.mkv"
            if target.is_file() and target.stat().st_size > 0:
                reused += 1
            else:
                tmp = target.with_name(target.stem + f".{token}.part.mkv")
                try:
                    _encode_span(tl, sp, src, kind, res, tmp, cancel)
                    os.replace(tmp, target)
                finally:
                    tmp.unlink(missing_ok=True)
                encoded += 1
            cached.append(target)
        if on_progress:
            on_progress(len(fl.spans), len(fl.spans), "assemblage")
        seconds = fl.total / tl.fps
        concat.write_text("".join("file '" + p.resolve().as_posix().replace("'", "'\\''") + "'\n" for p in cached), encoding="utf-8")
        mix = render_mix(tl, media_root, wav, duration=seconds, cancel=cancel)
        run([ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(wav),
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
             "-t", f"{seconds:.6f}", "-movflags", "+faststart", str(part)], cancel)
        actual = probe(part, cancel=cancel).duration
        if abs(actual - seconds) > 0.15:
            raise TimelineRenderError(f"durée inattendue : {actual:.2f} s au lieu de {seconds:.2f} s")
        if cancel:
            cancel.raise_if_cancelled()
        os.replace(part, out)
        return RenderReport(out, actual, fl.total, len(fl.spans), encoded, reused, res,
                            [i.message for i in issues if i.level == "warning"] + list(mix.warnings))
    except Cancelled:
        log.warning("export annulé ; les segments déjà encodés restent en cache")
        raise
    except FFmpegError:
        log.exception("export échoué")
        raise
    finally:
        part.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)
        concat.unlink(missing_ok=True)
