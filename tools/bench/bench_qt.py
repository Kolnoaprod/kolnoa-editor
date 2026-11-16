"""Prototype d'essai : interface Qt (PySide6). Rejoue le scénario partagé et écrit ses mesures en JSON.

Appelé par `bench_run.py` ; peut aussi se lancer seul :
    python bench_qt.py --scenario S.json --media-map M.json --peaks P.json --variant proxy --out R.json
Ne modifie aucun média. Les durées sont mesurées avec l'horloge murale ; la présentation GPU exacte n'est pas observable
depuis Qt : on mesure la livraison de l'image au widget vidéo (signal du QVideoSink) et le temps de peinture CPU du reste.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from PySide6.QtCore import QElapsedTimer, QEventLoop, QPointF, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

from bench_common import summarize

TRACK_H = 26
COLORS = [QColor("#3b6ea8"), QColor("#5a8f4a"), QColor("#a8623b"), QColor("#7b5aa8")]


class Timeline(QWidget):
    """800 plans, 2 pistes vidéo + 8 pistes audio avec formes d'onde, tête de lecture, rectangle de glisser-déposer."""

    def __init__(self, scenario: dict, peaks: list[int]):
        super().__init__()
        self.sc, self.peaks = scenario, peaks
        self.fps = scenario["fps"]
        self.zoom, self.offset = 0.4, 0.0        # pixels par image ; décalage en images
        self.playhead = 0
        self.drag: QRectF | None = None
        self.setMinimumSize(1100, TRACK_H * len(scenario["tracks"]) + 10)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d0f13"))
        w = self.width()
        for k in self.sc["clips"]:
            x = (k["start"] - self.offset) * self.zoom
            cw = k["dur"] * self.zoom
            if x > w or x + cw < 0:
                continue
            y = k["track"] * TRACK_H
            p.fillRect(QRectF(x, y + 1, max(1.0, cw - 1), TRACK_H - 2), COLORS[k["track"] % 4])
            if self.sc["tracks"][k["track"]]["kind"] == "audio" and cw > 8 and self.peaks:
                p.setPen(QPen(QColor(255, 255, 255, 120)))
                step = max(1, int(1 / max(self.zoom, 1e-6) / (self.fps * 0.02)))
                i0 = int((k["start"] / self.fps) / 0.02) % max(1, len(self.peaks) - 1)
                mid = y + TRACK_H / 2
                n = int(cw)
                for dx in range(0, n, 2):
                    amp = self.peaks[(i0 + dx * step) % len(self.peaks)] / 255 * (TRACK_H / 2 - 2)
                    p.drawLine(QPointF(x + dx, mid - amp), QPointF(x + dx, mid + amp))
        p.setPen(QPen(QColor("#ff5050"), 2))
        px = (self.playhead - self.offset) * self.zoom
        p.drawLine(QPointF(px, 0), QPointF(px, self.height()))
        if self.drag:
            p.fillRect(self.drag, QColor(255, 255, 255, 90))
        p.end()


class Bench:
    def __init__(self, a):
        self.a = a
        self.sc = json.load(open(a.scenario, encoding="utf-8"))
        self.paths = json.load(open(a.media_map, encoding="utf-8"))[a.variant]
        self.peaks = json.load(open(a.peaks, encoding="utf-8"))
        self.res = {"toolkit": "qt", "variant": a.variant, "metrics": {}, "phases": [], "errors": []}
        self.angles = self.sc["angles"]
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.win = QWidget()
        lay = QVBoxLayout(self.win)
        self.video = QVideoWidget()
        self.video.setMinimumSize(640, 360)
        self.timeline = Timeline(self.sc, self.peaks)
        lay.addWidget(self.video)
        lay.addWidget(self.timeline)
        self.sinks: dict[str, QVideoSink] = {}
        self.players: dict[str, QMediaPlayer] = {}
        self.counts: dict[str, int] = {}
        self.win_frames = 0
        self.last_win_frame_t = 0.0
        self.shown = self.angles[0]["id"]

    # --- outils ---------------------------------------------------------------------------------
    def phase(self, name):
        self.res["phases"].append({"name": name, "t0": time.time(), "t1": None})

    def end_phase(self):
        self.res["phases"][-1]["t1"] = time.time()

    def pump(self, ms):
        loop = QEventLoop()
        QTimer.singleShot(int(ms), loop.quit)
        loop.exec()

    def wait_frame(self, timeout_ms=3000):
        """Durée (ms) jusqu'à la prochaine image livrée au widget vidéo, ou None."""
        t0, before = time.perf_counter(), self.win_frames
        loop = QEventLoop()
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        tick = QTimer()
        tick.timeout.connect(lambda: loop.quit() if self.win_frames > before else None)
        tick.start(1)
        deadline.start(timeout_ms)
        loop.exec()
        tick.stop(); deadline.stop()
        return (time.perf_counter() - t0) * 1000 if self.win_frames > before else None

    def err(self, msg):
        self.res["errors"].append(msg)
        print("!", msg, file=sys.stderr)

    # --- préparation ----------------------------------------------------------------------------
    def build_players(self):
        for ang in self.angles:
            cid = ang["id"]
            pl = QMediaPlayer()
            ao = None
            if not self.a.no_audio:
                ao = QAudioOutput()
                ao.setVolume(0.0)                              # le décodage audio a lieu, sans bruit
                pl.setAudioOutput(ao)
            sink = QVideoSink()
            self.counts[cid] = 0
            sink.videoFrameChanged.connect(lambda _f, c=cid: self.counts.__setitem__(c, self.counts[c] + 1))
            pl.errorOccurred.connect(lambda e, s, c=cid: self.err(f"{c}: {s}"))
            pl.setSource(QUrl.fromLocalFile(self.paths[cid]))
            pl.setVideoSink(sink)
            pl._ao = ao
            self.players[cid], self.sinks[cid] = pl, sink
        wsink = self.video.videoSink()
        wsink.videoFrameChanged.connect(self._on_win_frame)

    def _on_win_frame(self, _f):
        self.win_frames += 1
        self.last_win_frame_t = time.time()

    def route(self, shown):
        """Le widget vidéo montre `shown` ; les autres angles continuent d'être décodés vers leur sink caché."""
        for cid, pl in self.players.items():
            pl.setVideoOutput(self.video if cid == shown else None)
            if cid != shown:
                pl.setVideoSink(self.sinks[cid])
        self.shown = shown

    def play_all(self):
        for pl in self.players.values():
            pl.play()

    def pause_all(self):
        for pl in self.players.values():
            pl.pause()

    def seek_all(self, ms):
        for pl in self.players.values():
            pl.setPosition(int(ms))

    # --- phases ---------------------------------------------------------------------------------
    def run(self):
        m = self.res["metrics"]
        t_launch = float(self.a.launch_epoch)
        self.build_players()
        self.route(self.shown)
        self.win.resize(1200, 800)
        self.phase("startup")
        self.win.show()
        self.play_all()
        w = self.wait_frame(15000)
        m["startup_s"] = round(self.last_win_frame_t - t_launch, 2) if w is not None else None
        if w is None:
            self.err("aucune image affichée au démarrage")
        self.end_phase()

        # décodage seul : aucune peinture de timeline, widget masqué du tour de table (les sinks comptent les images)
        self.phase("decode_only")
        for cid in self.counts:
            self.counts[cid] = 0
        self.seek_all(0); self.play_all()
        secs = 5.0
        self.pump(secs * 1000)
        tot = sum(self.counts.values())
        m["decode_only"] = {"angles": len(self.angles), "images_par_s_et_angle": round(tot / secs / len(self.angles), 1)}
        self.end_phase()

        # lecture avec interface + touches 1-9 (angles lus en parallèle)
        self.phase("ui_playback")
        self.seek_all(0); self.play_all()
        self.win_frames = 0
        play_ms = self.sc["play_s"] * 1000
        t_start = time.perf_counter()
        lat = []
        ph = QTimer()
        ph.timeout.connect(lambda: self._advance_playhead(t_start))
        ph.start(33)                                           # tête de lecture redessinée ~30 Hz
        for ev in self.sc["angle_events"]:
            self.pump(max(0, ev["t"] * 1000 - (time.perf_counter() - t_start) * 1000))
            target = self.angles[min(ev["key"], len(self.angles)) - 1]["id"]
            if target == self.shown:
                continue
            before = self.win_frames
            t0 = time.perf_counter()
            self.route(target)
            if self.wait_frame(2000) is not None:
                lat.append((time.perf_counter() - t0) * 1000)
        self.pump(max(0, play_ms - (time.perf_counter() - t_start) * 1000))
        ph.stop()
        elapsed = time.perf_counter() - t_start
        m["ui_playback"] = {"images_par_s": round(self.win_frames / elapsed, 1), "duree_s": round(elapsed, 1)}
        m["angle_switch_parallel_ms"] = summarize(lat)
        self.end_phase()

        # lecture / pause
        self.phase("play_pause")
        lat = []
        for _ in range(self.sc["play_pause"]):
            pl = self.players[self.shown]
            t0 = time.perf_counter()
            pl.pause()
            while pl.playbackState() != QMediaPlayer.PausedState and time.perf_counter() - t0 < 2:
                self.pump(1)
            lat.append((time.perf_counter() - t0) * 1000)
            self.pump(100)
            t0 = time.perf_counter()
            pl.play()
            if self.wait_frame(2000) is not None:
                lat.append((time.perf_counter() - t0) * 1000)
            self.pump(100)
        m["play_pause_ms"] = summarize(lat)
        self.end_phase()

        # scrub
        self.phase("scrub")
        pl = self.players[self.shown]
        pl.pause(); self.pump(200)
        lat = []
        for pos in self.sc["scrubs"]:
            t0 = time.perf_counter()
            pl.setPosition(int(pos * 1000))
            if self.wait_frame(2000) is not None:
                lat.append((time.perf_counter() - t0) * 1000)
        m["scrub_ms"] = summarize(lat)
        self.end_phase()

        # un seul lecteur qui change de source (alternative au décodage parallèle)
        self.phase("angle_switch_seek")
        for p in self.players.values():
            p.stop()
        single = QMediaPlayer()
        if not self.a.no_audio:
            ao = QAudioOutput(); ao.setVolume(0.0); single.setAudioOutput(ao)
        single.setVideoOutput(self.video)
        lat = []
        for ev in self.sc["angle_events"][:15]:
            cid = self.angles[min(ev["key"], len(self.angles)) - 1]["id"]
            t0 = time.perf_counter()
            single.setSource(QUrl.fromLocalFile(self.paths[cid]))
            single.setPosition(int(ev["t"] * 1000))
            single.play()
            if self.wait_frame(4000) is not None:
                lat.append((time.perf_counter() - t0) * 1000)
        single.stop()
        m["angle_switch_seek_ms"] = summarize(lat)
        self.end_phase()

        # timeline : zoom / déplacement
        self.phase("timeline_pan_zoom")
        tl, times = self.timeline, []
        for f in range(self.sc["pan_zoom_frames"]):
            tl.zoom = 0.1 + (f % 60) / 40
            tl.offset = f * 150
            t0 = time.perf_counter()
            tl.repaint()
            times.append((time.perf_counter() - t0) * 1000)
        m["timeline_pan_zoom_ms"] = summarize(times)
        self.end_phase()

        # glisser un plan I/O vers la timeline
        self.phase("drag_io")
        times = []
        for d in self.sc["drags"]:
            for step in range(20):
                tl.drag = QRectF(d["x0"] + d["dx"] * step / 20, d["y0"] + d["dy"] * step / 20, 90, TRACK_H - 2)
                t0 = time.perf_counter()
                tl.repaint()
                times.append((time.perf_counter() - t0) * 1000)
        tl.drag = None
        m["drag_io_ms"] = summarize(times)
        self.end_phase()
        self.win.close()

    def _advance_playhead(self, t_start):
        self.timeline.playhead = int((time.perf_counter() - t_start) * self.timeline.fps)
        self.timeline.update()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--media-map", required=True)
    ap.add_argument("--peaks", required=True)
    ap.add_argument("--variant", choices=["proxy", "source"], default="proxy")
    ap.add_argument("--out", required=True)
    ap.add_argument("--launch-epoch", default=str(time.time()))
    ap.add_argument("--no-audio", action="store_true", help="machine sans carte son (serveur, conteneur) : désactive la sortie audio")
    a = ap.parse_args()
    b = Bench(a)
    try:
        b.run()
    except Exception as exc:                               # un échec ne doit pas faire perdre les mesures déjà prises
        b.err(f"{type(exc).__name__}: {exc}")
    import PySide6
    b.res["qt"] = PySide6.__version__
    json.dump(b.res, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
