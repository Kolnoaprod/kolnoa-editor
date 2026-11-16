"""Essai de performance : interface Qt (PySide6). Ne modifie ni n'envoie rien.

Usage (Windows) :  pip install PySide6   puis   python bench_qt.py rush1.mp4 rush2.mp4 rush3.mp4
Mesure : coût de redessin d'une timeline de 200 puis 800 plans, délai de changement d'angle (seek), lecture simultanée.
"""

import json
import statistics
import sys
import time

from PySide6.QtCore import QEventLoop, QRectF, QTimer, QUrl
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QApplication


def pct(a, p):
    a = sorted(a)
    return round(a[min(len(a) - 1, int(len(a) * p))], 2)


def timeline(n):
    img = QImage(1800, 320, QImage.Format_ARGB32)
    clips = [(i * 90, 80 + (i % 7) * 4, i % 4) for i in range(n)]
    cols = [QColor("#3b6ea8"), QColor("#5a8f4a"), QColor("#a8623b"), QColor("#7b5aa8")]
    ts = []
    for f in range(240):
        t0 = time.perf_counter()
        zoom, off = 0.2 + (f % 60) / 60, f * 150
        p = QPainter(img)
        p.fillRect(img.rect(), QColor("#0d0f13"))
        for s, d, t in clips:
            x = (s - off) * zoom
            if 1800 >= x >= -d * zoom:
                p.fillRect(QRectF(x, 10 + t * 70, max(1, d * zoom - 1), 60), cols[t])
        p.end()
        ts.append((time.perf_counter() - t0) * 1000)
    return {"mediane": pct(ts, .5), "p95": pct(ts, .95), "max": pct(ts, 1)}


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def seeks(paths):
    players = []
    for p in paths[:3]:
        pl = QMediaPlayer()
        sink = QVideoSink()
        pl.setVideoSink(sink)
        pl.setSource(QUrl.fromLocalFile(p))
        pl.play(); wait(300); pl.pause()
        players.append((pl, sink))
    lat = []
    for i in range(30):
        pl, sink = players[i % len(players)]
        got = QEventLoop()
        sink.videoFrameChanged.connect(lambda _f, g=got: g.quit())
        t0 = time.perf_counter()
        pl.setPosition(1000 + (i * 370) % 8000)
        QTimer.singleShot(2000, got.quit)
        got.exec()
        lat.append((time.perf_counter() - t0) * 1000)
    frames = {"n": 0}
    for pl, sink in players:
        sink.videoFrameChanged.connect(lambda _f: frames.__setitem__("n", frames["n"] + 1))
        pl.setPosition(0); pl.play()
    wait(3000)
    for pl, _ in players:
        pl.pause()
    return ({"mediane": pct(lat, .5), "p95": pct(lat, .95), "max": pct(lat, 1)},
            {"angles": len(players), "images_presentees_par_s": round(frames["n"] / 3, 1)})


if __name__ == "__main__":
    app = QApplication(sys.argv)
    res = {"timeline_200_plans_ms": timeline(200), "timeline_800_plans_ms": timeline(800)}
    if len(sys.argv) > 1:
        res["angle_seek_ms"], res["lecture_simultanee"] = seeks(sys.argv[1:])
    print(json.dumps(res, indent=2, ensure_ascii=False))
