"""Géométrie de la timeline SANS Qt : conversions image ↔ pixel, zoom, rangées, plans visibles, hit-test.

Tout est testable sans écran. Le widget Qt (`timeline_view.py`) ne fait que dessiner ce que ce module calcule."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from ..timeline.model import Clip, Timeline

RULER_H = 24
ROW_H = {"video": 44, "audio": 34}
EDGE_PX = 6                      # zone de bord d'un plan (réservée au futur trim)
MIN_ZOOM, MAX_ZOOM = 0.002, 40.0  # pixels par image


@dataclass
class Viewport:
    zoom: float = 0.4            # pixels par image
    offset: float = 0.0          # première image visible (peut être fractionnaire)
    width: int = 1000

    def x_of(self, frame: float) -> float:
        return (frame - self.offset) * self.zoom

    def frame_of(self, x: float) -> float:
        return self.offset + x / self.zoom

    @property
    def last_frame(self) -> float:
        return self.frame_of(self.width)

    def zoom_at(self, x: float, factor: float) -> None:
        """Zoom centré sur le pixel x : l'image sous le curseur ne bouge pas."""
        anchor = self.frame_of(x)
        self.zoom = min(MAX_ZOOM, max(MIN_ZOOM, self.zoom * factor))
        self.offset = max(0.0, anchor - x / self.zoom)

    def pan_px(self, dx: float) -> None:
        self.offset = max(0.0, self.offset + dx / self.zoom)

    def fit(self, total_frames: int, margin_px: int = 20) -> None:
        total = max(1, total_frames)
        self.zoom = min(MAX_ZOOM, max(MIN_ZOOM, (self.width - 2 * margin_px) / total))
        self.offset = 0.0


class Rows:
    """Piste → ordonnée et hauteur. Les pistes gardent l'ordre de `tl.tracks`."""

    def __init__(self, tl: Timeline):
        self.ids = [t.id for t in tl.tracks]
        self.kinds = {t.id: t.kind for t in tl.tracks}
        self.y: dict[str, int] = {}
        y = RULER_H
        for t in tl.tracks:
            self.y[t.id] = y
            y += ROW_H.get(t.kind, 34)
        self.total_h = y

    def height(self, track_id: str) -> int:
        return ROW_H.get(self.kinds.get(track_id, "video"), 34)

    def track_at(self, y: float) -> str | None:
        for tid in self.ids:
            if self.y[tid] <= y < self.y[tid] + self.height(tid):
                return tid
        return None


class ClipIndex:
    """Plans de chaque piste triés par début, pour ne parcourir que les plans visibles (pas les 800)."""

    def __init__(self, tl: Timeline):
        self.by_track: dict[str, list[Clip]] = {}
        for c in tl.clips:
            self.by_track.setdefault(c.track, []).append(c)
        self.starts: dict[str, list[int]] = {}
        self.max_dur: dict[str, int] = {}
        for tid, lst in self.by_track.items():
            lst.sort(key=lambda c: c.start)
            self.starts[tid] = [c.start for c in lst]
            self.max_dur[tid] = max((c.duration for c in lst), default=0)

    def visible(self, track_id: str, f0: float, f1: float) -> list[Clip]:
        lst = self.by_track.get(track_id)
        if not lst:
            return []
        starts = self.starts[track_id]
        lo = bisect_left(starts, f0 - self.max_dur[track_id])
        hi = bisect_right(starts, f1)
        return [c for c in lst[lo:hi] if c.end > f0 and c.start < f1]


def hit_test(index: ClipIndex, rows: Rows, vp: Viewport, x: float, y: float):
    """Retourne (plan, zone) avec zone ∈ {'body', 'left', 'right'}, ou (None, 'none'). L'ordonnée de la règle n'est pas un plan."""
    tid = rows.track_at(y)
    if tid is None:
        return None, "none"
    f = vp.frame_of(x)
    for c in index.visible(tid, f - 1, f + 1):
        if c.start <= f < c.end:
            xl, xr = vp.x_of(c.start), vp.x_of(c.end)
            if xr - xl > 3 * EDGE_PX:
                if x - xl <= EDGE_PX:
                    return c, "left"
                if xr - x <= EDGE_PX:
                    return c, "right"
            return c, "body"
    return None, "none"


def ruler_step(zoom: float, fps: int) -> int:
    """Pas de graduation (en images) donnant au moins ~70 px entre deux graduations, sur des valeurs « rondes »."""
    for seconds in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600):
        if seconds * fps * zoom >= 70:
            return seconds * fps
    return 3600 * fps


def timecode(frame: int, fps: int) -> str:
    s, f = divmod(max(0, int(frame)), fps)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"
