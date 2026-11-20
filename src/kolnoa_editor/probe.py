"""Lecture des métadonnées média via ffprobe (avec cache mémoire par empreinte de fichier)."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from .ffmpeg import CancelToken, ffprobe_bin, run


@dataclass
class MediaInfo:
    duration: float
    width: int | None = None
    height: int | None = None
    fps: float | None = None          # cadence moyenne
    base_fps: float | None = None     # cadence de base (r_frame_rate)
    vcodec: str | None = None
    has_audio: bool = False
    audio_channels: int = 0
    sample_rate: int | None = None
    rotation: int = 0                 # degrés (0, 90, 180, 270) appliqués à l'affichage

    @property
    def is_vfr(self) -> bool:
        """Cadence variable : la cadence moyenne s'écarte de plus de 2 % de la cadence de base."""
        if not self.fps or not self.base_fps:
            return False
        return abs(self.fps - self.base_fps) / self.base_fps > 0.02

    @property
    def display_size(self) -> tuple[int, int] | None:
        """Taille affichée, rotation prise en compte (ffmpeg applique l'orientation seul)."""
        if not self.width or not self.height:
            return None
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_rate(rate: str | None) -> float | None:
    if not rate or "/" not in rate:
        return None
    num, den = rate.split("/", 1)
    try:
        n, d = float(num), float(den)
    except ValueError:
        return None
    return n / d if d else None


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"]))) % 360
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        try:
            return int(float(tag)) % 360
        except ValueError:
            return 0
    return 0


def probe(path: Path | str, *, cancel: CancelToken | None = None) -> MediaInfo:
    out = run(
        [
            ffprobe_bin(), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ], cancel=cancel, timeout=60
    ).stdout
    data = json.loads(out)
    info = MediaInfo(duration=float(data.get("format", {}).get("duration") or 0.0))
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and info.vcodec is None:
            info.vcodec = stream.get("codec_name")
            info.width = stream.get("width")
            info.height = stream.get("height")
            info.fps = _parse_rate(stream.get("avg_frame_rate"))
            info.base_fps = _parse_rate(stream.get("r_frame_rate"))
            if info.fps is None:
                info.fps = info.base_fps
            info.rotation = _rotation(stream)
        elif kind == "audio":
            if not info.has_audio:
                info.sample_rate = int(stream.get("sample_rate") or 0) or None
            info.has_audio = True
            info.audio_channels = max(info.audio_channels, int(stream.get("channels") or 0))
    return info


_CACHE: dict[tuple[str, int, int], MediaInfo] = {}


def probe_cached(path: Path | str, *, cancel=None) -> MediaInfo:
    """probe() mémorisé tant que le fichier (taille + date) ne change pas."""
    p = Path(path)
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key not in _CACHE:
        _CACHE[key] = probe(p) if cancel is None else probe(p, cancel=cancel)
    return _CACHE[key]
