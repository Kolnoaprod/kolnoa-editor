"""Lecture des métadonnées média via ffprobe."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

from .ffmpeg import ffprobe_bin, run


@dataclass
class MediaInfo:
    duration: float
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    has_audio: bool = False
    audio_channels: int = 0

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


def probe(path: Path | str) -> MediaInfo:
    out = run(
        [
            ffprobe_bin(), "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
    ).stdout
    data = json.loads(out)
    info = MediaInfo(duration=float(data.get("format", {}).get("duration") or 0.0))
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and info.vcodec is None:
            info.vcodec = stream.get("codec_name")
            info.width = stream.get("width")
            info.height = stream.get("height")
            info.fps = _parse_rate(stream.get("avg_frame_rate")) or _parse_rate(
                stream.get("r_frame_rate")
            )
        elif kind == "audio":
            info.has_audio = True
            info.audio_channels = max(info.audio_channels, int(stream.get("channels") or 0))
    return info
