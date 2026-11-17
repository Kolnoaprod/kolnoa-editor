"""Widget de timeline PySide6, branché sur le vrai modèle (`Timeline` + `Session`).

Choix de performance (mesurés par le benchmark : 8 ms de redessin, 100 % d'un cœur au pan/zoom) :
- seuls les plans visibles sont dessinés (index trié + recherche dichotomique) ;
- les formes d'onde sont calculées en numpy par colonnes de pixels depuis une pyramide de crêtes, puis posées en une seule image ;
- la tête de lecture ne redessine que sa bande (pas toute la timeline) ;
- aucune modification directe : un glisser valide appelle `move_clips` puis `Session.do` (une étape d'annulation).
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..timeline.editing import linked_ids, move_clips, razor, snap_frame, snap_move, snap_points, trim_edge, trim_limits
from ..timeline.model import Clip, Timeline, TimelineError
from ..timeline.session import Session
from . import layout as L
from .peaks import PeakPyramid
from .shortcuts import Shortcuts

BG, RULER_BG, GRID = QColor("#0d0f13"), QColor("#161a21"), QColor("#232a35")
CLIP_COLORS = {"video": QColor("#3b6ea8"), "audio": QColor("#3f7f5f")}
SELECTED, PLAYHEAD, GHOST = QColor("#ffd24a"), QColor("#ff5050"), QColor(255, 255, 255, 90)
SNAP_PX = 8


class TimelineView(QWidget):
    playheadChanged = Signal(int)
    selectionChanged = Signal(list)
    edited = Signal(str)               # libellé de l'action validée dans la session
    rejected = Signal(str)             # action refusée par le moteur (chevauchement, piste verrouillée…)

    def __init__(self, session: Session, peaks_provider=None, parent=None, source_len=None, shortcuts=None):
        super().__init__(parent)
        self.session = session
        self.shortcuts = shortcuts or Shortcuts()
        self.source_len = source_len or (lambda media: None)           # media -> durée du rush en secondes | None
        self.peaks_provider = peaks_provider or (lambda media: None)   # media -> PeakPyramid | None
        self.vp = L.Viewport()
        self.playhead = 0
        self.selection: list[str] = []
        self._drag: dict | None = None
        self._trim: dict | None = None
        self._scrub = False
        self._wave_cache: dict = {}
        self.stats = {"paint_ms": 0.0, "clips_drawn": 0}
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.refresh_model()
        self.setMinimumHeight(self.rows.total_h + 4)

    # --- modèle ---------------------------------------------------------------------------------------------
    @property
    def tl(self) -> Timeline:
        return self.session.tl

    def refresh_model(self) -> None:
        """À appeler après toute modification du modèle (le widget ne garde aucune copie des plans)."""
        self.rows = L.Rows(self.tl)
        self.index = L.ClipIndex(self.tl)
        self.selection = [c for c in self.selection if any(k.id == c for k in self.tl.clips)]
        self.setMinimumHeight(self.rows.total_h + 4)
        self.update()

    def set_playhead(self, frame: int, emit: bool = True) -> None:
        frame = max(0, int(frame))
        if frame == self.playhead:
            return
        old = self.playhead
        self.playhead = frame
        for f in (old, frame):             # ne redessine que les deux bandes de la tête de lecture
            x = int(self.vp.x_of(f))
            self.update(QRect(x - 2, 0, 5, self.height()))
        if emit:
            self.playheadChanged.emit(frame)

    def fit(self) -> None:
        self.vp.width = self.width()
        self.vp.fit(self.tl.end_frame())
        self.update()

    # --- dessin ---------------------------------------------------------------------------------------------
    def resizeEvent(self, e):
        self.vp.width = self.width()
        super().resizeEvent(e)

    def paintEvent(self, ev):
        import time
        t0 = time.perf_counter()
        p = QPainter(self)
        p.fillRect(ev.rect(), BG)
        self._paint_rows(p)
        self._paint_clips(p)
        self._paint_ghost(p)
        self._paint_trim(p)
        self._paint_ruler(p)
        x = self.vp.x_of(self.playhead)
        p.setPen(QPen(PLAYHEAD, 2))
        p.drawLine(QPoint(int(x), 0), QPoint(int(x), self.height()))
        p.end()
        self.stats["paint_ms"] = (time.perf_counter() - t0) * 1000

    def _paint_rows(self, p: QPainter) -> None:
        p.setPen(QPen(GRID))
        for tid in self.rows.ids:
            y = self.rows.y[tid] + self.rows.height(tid) - 1
            p.drawLine(0, y, self.width(), y)

    def _paint_ruler(self, p: QPainter) -> None:
        p.fillRect(0, 0, self.width(), L.RULER_H, RULER_BG)
        fps = self.tl.fps
        step = L.ruler_step(self.vp.zoom, fps)
        first = int(self.vp.offset // step) * step
        p.setPen(QPen(QColor("#9aa4b2")))
        p.setFont(QFont("Segoe UI", 8))
        f = first
        while f <= self.vp.last_frame:
            x = int(self.vp.x_of(f))
            p.drawLine(x, L.RULER_H - 8, x, L.RULER_H)
            p.drawText(x + 3, 12, L.timecode(f, fps))
            f += step

    def _paint_clips(self, p: QPainter) -> None:
        f0, f1 = self.vp.offset, self.vp.last_frame
        drawn = 0
        for tid in self.rows.ids:
            y, h = self.rows.y[tid], self.rows.height(tid)
            kind = self.rows.kinds[tid]
            for c in self.index.visible(tid, f0, f1):
                if self._drag and c.id in self._drag["moving"]:
                    continue
                xl, xr = self.vp.x_of(c.start), self.vp.x_of(c.end)
                rect = QRectF(xl, y + 1, max(1.0, xr - xl - 1), h - 2)
                p.fillRect(rect, CLIP_COLORS.get(kind, CLIP_COLORS["video"]))
                if kind == "audio" and xr - xl > 4:
                    self._paint_wave(p, c, xl, xr, y, h)
                if c.id in self.selection:
                    p.setPen(QPen(SELECTED, 2))
                    p.drawRect(rect.adjusted(1, 1, -1, -1))
                if xr - xl > 40:
                    p.setPen(QPen(QColor("#e8edf5")))
                    p.drawText(rect.adjusted(4, 2, -2, -2), Qt.AlignLeft | Qt.AlignTop, c.label or c.id)
                drawn += 1
        self.stats["clips_drawn"] = drawn

    def _paint_wave(self, p: QPainter, c: Clip, xl: float, xr: float, y: int, h: int) -> None:
        pyr: PeakPyramid | None = self.peaks_provider(c.media)
        if pyr is None:
            return
        x0, x1 = max(0, int(xl)), min(self.width(), int(xr))
        n = x1 - x0
        if n <= 0:
            return
        whole = xl >= 0 and xr <= self.width()                    # plan entièrement visible : son image ne dépend pas du pan
        key = (c.media, c.src_in, c.duration, round(self.vp.zoom, 6), h, int(xr) - int(xl))
        if whole and key in self._wave_cache:
            p.drawImage(int(xl), y, self._wave_cache[key])
            return
        spp = 1.0 / (self.vp.zoom * self.tl.fps)                 # secondes par pixel
        t_start = c.src_in + (self.vp.frame_of(x0) - c.start) / self.tl.fps
        amp = pyr.columns(t_start, spp, n).astype(np.int32) * (h // 2 - 2) // 255
        rows = np.arange(h)[:, None]
        mask = np.abs(rows - h // 2) <= amp[None, :]
        img = np.zeros((h, n, 4), dtype=np.uint8)
        img[mask] = (255, 255, 255, 150)
        img = np.ascontiguousarray(img)
        qi = QImage(img.data, n, h, n * 4, QImage.Format_RGBA8888).copy()      # copie : le tampon numpy ne survit pas
        if whole:
            if len(self._wave_cache) > 1500:
                self._wave_cache.clear()
            self._wave_cache[key] = qi
            p.drawImage(int(xl), y, qi)
        else:
            p.drawImage(x0, y, qi)

    def _paint_ghost(self, p: QPainter) -> None:
        d = self._drag
        if not d or not d["active"]:
            return
        for cid, (start, tid) in d["preview"].items():
            c = self.tl.clip(cid)
            y, h = self.rows.y.get(tid, 0), self.rows.height(tid)
            p.fillRect(QRectF(self.vp.x_of(start), y + 1, c.duration * self.vp.zoom - 1, h - 2), GHOST)

    # --- souris ---------------------------------------------------------------------------------------------
    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.vp.zoom_at(e.position().x(), 1.25 if e.angleDelta().y() > 0 else 0.8)
        else:
            self.vp.pan_px(-(e.angleDelta().x() or e.angleDelta().y()))
        self.update()
        e.accept()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        x, y = e.position().x(), e.position().y()
        self.setFocus()
        if y < L.RULER_H:
            self._scrub = True
            self.set_playhead(round(self.vp.frame_of(x)))
            return
        c, zone = L.hit_test(self.index, self.rows, self.vp, x, y)
        if c is None:
            self._select([])
            return
        if e.modifiers() & Qt.ShiftModifier:
            self._select(self.selection + [c.id] if c.id not in self.selection else [s for s in self.selection if s != c.id])
        elif c.id not in self.selection:
            self._select([c.id])
        if zone in ("left", "right"):
            edge = "head" if zone == "left" else "tail"
            try:
                lo, hi = trim_limits(self.tl, c.id, edge, True, self.source_len)
            except TimelineError as err:
                self.rejected.emit(str(err))
                return
            self._trim = {"id": c.id, "edge": edge, "x0": x, "lo": lo, "hi": hi, "delta": 0, "active": False, "snapped": False}
            return
        if zone == "body" and not c.locked:
            moving = set(linked_ids(self.tl, c.id))
            self._drag = {"id": c.id, "x0": x, "start0": c.start, "track0": c.track, "moving": moving,
                          "active": False, "preview": {}}

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if self._scrub:
            self.set_playhead(round(self.vp.frame_of(x)))
            return
        if self._trim:
            self._trim_move(x)
            return
        d = self._drag
        if not d:
            self._update_cursor(x, y)
            return
        if not d["active"] and abs(x - d["x0"]) < 4:
            return
        d["active"] = True
        c = self.tl.clip(d["id"])
        raw = d["start0"] + round((x - d["x0"]) / self.vp.zoom)
        start = snap_move(self.tl, d["id"], max(0, raw), max(1, round(SNAP_PX / self.vp.zoom)), self.playhead)
        tid = self.rows.track_at(y) or d["track0"]
        if self.rows.kinds.get(tid) != self.rows.kinds.get(c.track):
            tid = d["track0"]                                     # on ne glisse pas une vidéo sur une piste audio
        d["target"] = (max(0, start), tid)
        delta = d["target"][0] - c.start
        d["preview"] = {cid: (self.tl.clip(cid).start + delta, tid if cid == d["id"] else self.tl.clip(cid).track)
                        for cid in d["moving"]}
        self.update()

    def _update_cursor(self, x: float, y: float) -> None:
        if y < L.RULER_H:
            return self.unsetCursor()
        c, zone = L.hit_test(self.index, self.rows, self.vp, x, y)
        self.setCursor(Qt.SizeHorCursor if (c is not None and zone in ("left", "right") and not c.locked) else Qt.ArrowCursor)

    # --- rognage des bords ----------------------------------------------------------------------------------
    def _trim_move(self, x: float) -> None:
        t = self._trim
        if not t["active"] and abs(x - t["x0"]) < 3:
            return
        t["active"] = True
        c = self.tl.clip(t["id"])
        edge_frame = c.start if t["edge"] == "head" else c.end
        raw = edge_frame + round((x - t["x0"]) / self.vp.zoom)
        pts = snap_points(self.tl, exclude=set(linked_ids(self.tl, c.id)), playhead=self.playhead)
        snapped = snap_frame(raw, pts, max(1, round(SNAP_PX / self.vp.zoom)))
        t["snapped"] = snapped != raw
        t["delta"] = max(t["lo"], min(t["hi"], snapped - edge_frame))   # la prévisualisation ne sort jamais des limites permises
        t["clamped"] = (snapped - edge_frame) != t["delta"]
        self.update()

    def _trim_finish(self) -> None:
        t, self._trim = self._trim, None
        if t and t["active"] and t["delta"] != 0:
            try:
                tx = trim_edge(self.tl, t["id"], t["edge"], t["delta"], True, self.source_len)
                self.session.do(tx, tx.label)
                self.refresh_model()
                self.edited.emit(tx.label)
            except TimelineError as err:
                self.rejected.emit(str(err))
        self.update()

    def _paint_trim(self, p: QPainter) -> None:
        t = self._trim
        if not t or not t["active"]:
            return
        for cid in linked_ids(self.tl, t["id"]):
            c = self.tl.clip(cid)
            start, end = (c.start + t["delta"], c.end) if t["edge"] == "head" else (c.start, c.end + t["delta"])
            y, h = self.rows.y.get(c.track, 0), self.rows.height(c.track)
            p.fillRect(QRectF(self.vp.x_of(start), y + 1, (end - start) * self.vp.zoom - 1, h - 2), GHOST)
            edge_x = self.vp.x_of(start if t["edge"] == "head" else end)
            p.setPen(QPen(QColor("#ff9f1c") if t.get("clamped") else QColor("#ffd24a"), 2))
            p.drawLine(int(edge_x), y, int(edge_x), y + h)
        c = self.tl.clip(t["id"])
        p.setPen(QColor("#e8eaed"))
        txt = f"{t['delta']:+d} i  ·  {L.timecode((c.start + t['delta']) if t['edge'] == 'head' else (c.end + t['delta']), self.tl.fps)}"
        if t.get("clamped"):
            txt += "  (limite)"
        p.drawText(int(self.vp.x_of(c.start if t["edge"] == "head" else c.end)) + 6, self.rows.y.get(c.track, 0) + 14, txt)

    def mouseReleaseEvent(self, e):
        self._scrub = False
        if self._trim:
            self._trim_finish()
            return
        d, self._drag = self._drag, None
        if d and d["active"] and d.get("target"):
            start, tid = d["target"]
            c = self.tl.clip(d["id"])
            if (start, tid) != (c.start, c.track):
                try:
                    tx = move_clips(self.tl, d["id"], start, tid if tid != c.track else None)
                    self.session.do(tx, tx.label if hasattr(tx, "label") else "Déplacement")
                    self.refresh_model()
                    self.edited.emit("Déplacement")
                except TimelineError as err:
                    self.rejected.emit(str(err))
        self.update()

    # --- actions du moteur déclenchées au clavier -----------------------------------------------------------
    def _run(self, make_tx, label_fallback: str) -> None:
        """Construit la transaction auprès du moteur, l'applique en UNE étape d'annulation ; un refus ne change rien."""
        try:
            tx = make_tx()
            self.session.do(tx, tx.label if hasattr(tx, "label") else label_fallback)
        except TimelineError as err:
            self.rejected.emit(str(err))
            return
        self.refresh_model()
        self.edited.emit(tx.label if hasattr(tx, "label") else label_fallback)
        self.update()

    def razor_at_playhead(self, all_tracks: bool = False) -> None:
        """Coupe à la tête de lecture. Avec une sélection : seulement les pistes des plans sélectionnés (leurs liés suivent) ;
        sans sélection (ou `all_tracks`) : toutes les pistes déverrouillées. Les moitiés gardent source, gain, fondus extérieurs,
        angle multicam, routage audio, notes et réglages (le moteur copie tout le plan)."""
        tracks = None
        if self.selection and not all_tracks:
            tracks = sorted({self.tl.clip(c).track for c in self.selection if any(k.id == c for k in self.tl.clips)})
        self._run(lambda: razor(self.tl, self.playhead, tracks), "Rasoir")

    def undo(self) -> None:
        try:
            label = self.session.undo()
        except TimelineError as err:
            self.rejected.emit(str(err))
            return
        self.refresh_model()
        self.edited.emit(f"Annuler : {label}")
        self.update()

    def redo(self) -> None:
        try:
            label = self.session.redo()
        except TimelineError as err:
            self.rejected.emit(str(err))
            return
        self.refresh_model()
        self.edited.emit(f"Rétablir : {label}")
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        action = self.shortcuts.action_for(e)
        if action in ("razor", "razor_all"):
            self.razor_at_playhead(all_tracks=action == "razor_all")
            return
        if action == "undo":
            return self.undo()
        if action in ("redo", "redo_alt"):
            return self.redo()
        if k == Qt.Key_Escape and (self._drag or self._trim):
            self._drag = self._trim = None
            self.update()
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.vp.zoom_at(self.width() / 2, 1.25)
            self.update()
        elif k == Qt.Key_Minus:
            self.vp.zoom_at(self.width() / 2, 0.8)
            self.update()
        elif k == Qt.Key_Home:
            self.set_playhead(0)
        elif k == Qt.Key_End:
            self.set_playhead(self.tl.end_frame)
        else:
            super().keyPressEvent(e)

    def _select(self, ids: list[str]) -> None:
        if ids != self.selection:
            self.selection = ids
            self.selectionChanged.emit(list(ids))
            self.update()
