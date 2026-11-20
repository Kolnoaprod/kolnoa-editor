"""Signaux de test déterministes pour la synchronisation (tests, bench, exemple reproductible).

Un « enregistreur » (maître) est synthétisé, puis des « caméras » en sont extraites avec décalage, dérive d'horloge,
bruit, silences, fréquence d'échantillonnage et couleur différents. La vérité terrain est toujours connue.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from ..ffmpeg import ffmpeg_bin, run_hidden


def speech_like(seconds: float, seed: int, rate: int = 8000) -> np.ndarray:
    """Bruit large bande haché en « syllabes » d'amplitudes variées, avec quelques claquements : ressemble à de
    la parole en énergie (enveloppe structurée), sans en être."""
    r = np.random.default_rng(seed)
    n = int(seconds * rate)
    x = r.standard_normal(n)
    env = np.zeros(n)
    i = 0
    while i < n:
        length = int(r.uniform(0.1, 0.5) * rate)
        env[i:i + length] = r.uniform(0.2, 1.0)
        i += length + int(r.uniform(0.05, 0.6) * rate)
    sig = x * env
    for pos in r.uniform(0, seconds, size=max(1, int(seconds // 20))):   # claquements de mains
        k = int(pos * rate)
        sig[k:k + int(0.01 * rate)] += r.standard_normal(min(int(0.01 * rate), n - k)) * 4
    return (sig / (np.abs(sig).max() + 1e-9)).astype(np.float32)


def extract(master: np.ndarray, master_rate: int, offset_s: float, duration_s: float, *, out_rate: int | None = None,
            drift_ppm: float = 0.0, snr_db: float | None = None, gain: float = 1.0, color: bool = False,
            silence: list[tuple[float, float]] | None = None, seed: int = 0) -> np.ndarray:
    """Ce qu'une caméra enregistrerait : t_groupe = offset + t_angle × (1 + dérive). Les parties hors du maître
    sont du bruit propre à la caméra (la caméra tournait avant/après l'enregistreur)."""
    out_rate = out_rate or master_rate
    r = np.random.default_rng(seed + 1000)
    t_a = np.arange(int(duration_s * out_rate)) / out_rate
    t_g = offset_s + t_a * (1 + drift_ppm * 1e-6)
    grid = np.arange(len(master)) / master_rate
    inside = (t_g >= 0) & (t_g <= grid[-1])
    x = np.interp(t_g, grid, master.astype(np.float64))
    x = np.where(inside, x, r.standard_normal(len(t_a)) * 0.05)
    if color:                                   # micro différent : passe-bas léger + écho court
        k = np.ones(5) / 5
        x = np.convolve(x, k, mode="same")
        d = int(0.004 * out_rate)
        x[d:] += 0.3 * x[:-d]
    x = x * gain
    for a, b in silence or []:
        x[int(a * out_rate): int(b * out_rate)] = 0.0
    if snr_db is not None:
        p = float(np.mean(x ** 2)) + 1e-12
        x = x + r.standard_normal(len(x)) * np.sqrt(p / 10 ** (snr_db / 10))
    return x.astype(np.float32)


def write_wav(path: Path, samples: np.ndarray, rate: int) -> Path:
    """WAV mono 16 bits (lisible par ffmpeg)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    return path


def luma_scene(seconds: float, fps: float, seed: int, events: list[float] | None = None,
               private_events: int = 0, noise: float = 0.6) -> np.ndarray:
    """Série de luminosité d'une scène : lente variation + bruit + événements lumineux (flash de 0,2 s) aux
    instants `events` (communs aux caméras) + `private_events` événements propres à cette caméra."""
    r = np.random.default_rng(seed)
    n = int(seconds * fps)
    t = np.arange(n) / fps
    x = 90 + 6 * np.sin(2 * np.pi * t / r.uniform(40, 90)) + r.standard_normal(n) * noise
    for e in list(events or []) + list(r.uniform(2, seconds - 2, size=private_events)):
        k = int(e * fps)
        x[k:k + max(1, int(0.2 * fps))] += r.uniform(35, 60)
    return x


def write_gray_video(path: Path, luma: np.ndarray, fps: float, size=(64, 36), with_audio: bool = False) -> Path:
    """Vidéo H.264 dont la luminosité suit `luma` (une image par échantillon), avec un motif fixe propre à la scène."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    pattern = (np.sin(xx / 5.0) * np.cos(yy / 4.0)) * 12
    frames = np.clip(luma[:, None, None] + pattern[None], 0, 255).astype(np.uint8)
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-shortest", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-g", "5", str(path)]
    run_hidden(cmd, input=frames.tobytes(), check=True)
    return path


def mux_audio(video: Path, wav: Path, out: Path) -> Path:
    """Colle une piste audio (WAV) sur une vidéo : fabrique une « caméra » avec image et son."""
    run_hidden([ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", "-i", str(video), "-i", str(wav),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", str(out)], check=True)
    return Path(out)
