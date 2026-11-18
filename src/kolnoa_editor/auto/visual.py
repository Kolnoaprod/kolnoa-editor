"""Analyse visuelle LOCALE et légère d'un angle (aucune API, aucune identité de personne) : échantillonnage à basse résolution (1 image par
`slot_s`, 160×90 en gris) en UNE passe ffmpeg par angle, puis mesures numpy :
  - netteté (variance du laplacien) — relative entre angles, pas absolue ;
  - exposition (luminance moyenne, part d'ombres bouchées / hautes lumières brûlées) ;
  - visages : OPTIONNEL (OpenCV s'il est installé), désactivé par défaut ; sinon « non disponible ».
Non mesuré : stabilité (échantillonnage trop espacé pour séparer bougé et changement de plan), contenu, expressions, qui est qui.
Les résultats sont mis en cache sur disque (clé = fichier + plage + paramètres) ; l'analyse peut être éteinte."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..ffmpeg import ffmpeg_bin


@dataclass(frozen=True)
class VisualParams:
    enabled: bool = True
    slot_s: float = 1.0
    width: int = 160
    height: int = 90
    faces: bool = False
    # choix d'angle (hystérésis) — valeurs de départ à CALIBRER sur les rushes réels
    switch_penalty: float = 0.35         # coût d'un changement d'angle (en « qualité-secondes »)
    pause_penalty: float = 0.15          # coût réduit quand la coupe tombe dans une pause de la parole
    min_dwell_s: float = 3.0             # durée minimale d'un plan
    max_dwell_s: float | None = None     # None = aucune coupe imposée par la durée
    master_bonus: float = 0.04           # à qualité égale, on garde le maître
    w_sharp: float = 0.55
    w_expo: float = 0.45


@dataclass
class Sample:
    t_angle: float
    sharp: float
    mean: float
    dark: float
    bright: float
    faces: float | None = None           # part de l'image couverte par des visages, si mesurée


@dataclass
class AnalysisStats:
    angles: int = 0
    frames: int = 0
    seconds: float = 0.0
    cache_hits: int = 0
    peak_rss_mb: float | None = None
    notes: list[str] = field(default_factory=list)


def laplacian_var(g: np.ndarray) -> float:
    g = g.astype(np.float32)
    lap = g[1:-1, 1:-1] * -4 + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(lap.var())


def frame_metrics(g: np.ndarray) -> tuple[float, float, float, float]:
    return laplacian_var(g), float(g.mean()), float((g < 20).mean()), float((g > 235).mean())


def _faces(g: np.ndarray) -> float | None:
    try:
        import cv2
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if casc.empty():
            return None
        boxes = casc.detectMultiScale(g, 1.1, 4)
        return float(sum(w * h for (_, _, w, h) in boxes) / g.size)
    except Exception:                                    # noqa: BLE001 — pas d'OpenCV : critère simplement indisponible
        return None


def _rss_mb() -> float | None:
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except Exception:                                    # noqa: BLE001
        return None


def analyze_angle(path: Path, t_from: float, t_to: float, p: VisualParams, cache_dir: Path | None = None,
                  stats: AnalysisStats | None = None) -> list[Sample]:
    """Échantillons de `path` entre `t_from` et `t_to` (secondes de l'angle), un par `slot_s`."""
    path = Path(path)
    if t_to - t_from < p.slot_s * 0.5:
        return []
    st = path.stat()
    key = hashlib.sha1(json.dumps([str(path.resolve()), st.st_size, st.st_mtime_ns, round(t_from, 3), round(t_to, 3), p.slot_s, p.width, p.height,
                                   p.faces, 3]).encode()).hexdigest()[:20]
    cache = (cache_dir / f"{key}.json") if cache_dir else None
    if cache and cache.is_file():
        try:
            out = [Sample(**d) for d in json.loads(cache.read_text(encoding="utf-8"))]
            if stats:
                stats.cache_hits += 1
                stats.angles += 1
                stats.frames += len(out)
            return out
        except (OSError, ValueError, TypeError):
            pass
    t0 = time.perf_counter()
    cmd = [ffmpeg_bin(), "-v", "error", "-ss", f"{t_from:.3f}", "-t", f"{t_to - t_from:.3f}", "-i", str(path), "-an", "-vf",
           f"fps=1/{p.slot_s},scale={p.width}:{p.height}", "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"analyse visuelle ffmpeg échouée pour {path}: {r.stderr.decode(errors='replace')[-1000:]}")
    n = p.width * p.height
    buf = r.stdout
    out: list[Sample] = []
    for i in range(len(buf) // n):
        g = np.frombuffer(buf, dtype=np.uint8, count=n, offset=i * n).reshape(p.height, p.width)
        s, m, d, b = frame_metrics(g)
        out.append(Sample(round(t_from + (i + 0.5) * p.slot_s, 3), s, m, d, b, _faces(g) if p.faces else None))
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps([s.__dict__ for s in out]), encoding="utf-8")
    if stats:
        stats.angles += 1
        stats.frames += len(out)
        stats.seconds += time.perf_counter() - t0
        rss = _rss_mb()
        if rss is not None:
            stats.peak_rss_mb = max(stats.peak_rss_mb or 0.0, rss)
    return out
