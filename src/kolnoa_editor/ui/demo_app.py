"""Application de TEST pour Windows : on choisit 1 à 9 vidéos, elles deviennent un groupe multicam sur une timeline Qt.

On peut y essayer en vrai : lecteur multicam (touches 1-9, Maj+Espace = enregistrer une prise, Entrée = valider), rognage des bords
(Alt = ripple), rasoir (S), glisser depuis le chutier, formes d'onde asynchrones, annuler/rétablir — et lancer les mesures (4K50, GPU).
Rien n'écrit dans les rushes : la session de montage et les proxys vivent dans un dossier de travail (par défaut Documents/Kolnoa_Test).
Ce n'est PAS l'éditeur final : c'est la fenêtre qui permet de juger le ressenti et de mesurer sur la vraie machine.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from ..probe import probe
from ..timeline import Clip, Timeline, Track, add_multicam_clip, mark_subclip
from ..timeline.model import Angle, MulticamGroup
from ..timeline.ops import AddGroup, execute
from ..timeline.session import Session

FPS = 25
PROXY_SECONDS = 90          # les proxys ne couvrent que le début des rushes (essai) : évite d'encoder des heures


def default_work_dir() -> Path:
    base = Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents" / "Kolnoa_Test"
    return base / time.strftime("%Y-%m-%d_%H%M%S")


def make_test_proxy(src: str, dst: Path, height: int = 540, seconds: int = PROXY_SECONDS, cancel=None) -> bool:
    """Proxy léger (H.264, 25 i/s, images intra fréquentes → seek rapide) des `seconds` premières secondes. Jamais d'écriture dans la source."""
    from ..ffmpeg import ffmpeg_bin
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part.mp4")
    cmd = [ffmpeg_bin(), "-y", "-v", "error", "-i", src, "-t", str(seconds), "-vf", f"scale=-2:{height},fps=25", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "26", "-g", "12", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", str(tmp)]
    kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    while p.poll() is None:
        if cancel and cancel():
            p.kill()
            p.wait()
            tmp.unlink(missing_ok=True)
            return False
        time.sleep(0.2)
    if p.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        return False
    os.replace(tmp, dst)
    return True


def build_demo(videos: list[str], work: Path, make_proxies: bool = True, log=lambda s: None, progress=lambda f: None,
               cancel=None, proxy_seconds: int = PROXY_SECONDS):
    """Retourne (Session, media: dict[angle → MediaInfo du planificateur], durées). Les angles sont calés à zéro (même départ) :
    c'est un banc d'essai du lecteur, pas une synchronisation (qui existe dans le moteur de synchro)."""
    from ..player.scheduler import MediaInfo
    work.mkdir(parents=True, exist_ok=True)
    infos = [probe(v) for v in videos]
    dur = min(i.duration for i in infos)
    n = min(9, len(videos))
    tl = Timeline(title="Essai lecteur", fps=FPS, tracks=[Track("V1", "video"), Track("V2", "video"), Track("A1", "audio"), Track("A2", "audio")])
    angles = [Angle(id=f"CAM{i + 1}", media=str(Path(v).resolve()), kind="video", has_audio=infos[i].has_audio or i == 0,
                    offset=0.0, confidence=1.0, status="ok", method="manual") for i, v in enumerate(videos[:n])]
    angles[0].has_audio = True
    g = MulticamGroup(id="g001", master="CAM1", name="Essai", angles=angles)
    execute(tl, AddGroup(asdict(g)))
    frames = max(25, int(dur * FPS))
    execute(tl, add_multicam_clip(tl, "g001", "CAM1", "V1", 0, frames, group_in=0.0, clip_id="s001"))
    for i, v in enumerate(videos[:n]):
        full = str(Path(v).resolve())
        if infos[i].has_audio and i < 2:                       # formes d'onde : le son des deux premières caméras sur A1 / A2
            tl.clips.append(Clip(f"w{i + 1}", f"A{i + 1}", full, 0, min(frames, FPS * 600), src_in=0.0, label=f"son CAM{i + 1}"))
        execute(tl, mark_subclip(tl, full, 0.0, min(10.0, infos[i].duration), f"CAM{i + 1} · 10 s", source_len=infos[i].duration))
    tl.validate()
    sess = Session.create(work / "session", tl)
    media: dict = {}
    for i, v in enumerate(videos[:n]):
        a = f"CAM{i + 1}"
        proxy = None
        if make_proxies:
            dst = work / "proxies" / f"{a}.proxy.mp4"
            log(f"Proxy {a} ({i + 1}/{n})…")
            if make_test_proxy(str(v), dst, seconds=proxy_seconds, cancel=cancel):
                proxy = str(dst)
            progress((i + 1) / n)
        w, h = infos[i].display_size or (1920, 1080)
        media[a] = MediaInfo(str(Path(v).resolve()), w, h, infos[i].fps or 25.0, proxy, 540, infos[i].duration)
    return sess, media, {f"CAM{i + 1}": infos[i].duration for i in range(n)}


def _make_window(sess: Session, media: dict, durations: dict, work: Path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton, QSplitter, QVBoxLayout, QWidget)

    from ..player.controller import MulticamPlayer
    from ..player.qt_backend import configure_hw
    from .bin_view import BinView
    from .multicam_view import MulticamView
    from .peak_service import PeakService
    from .timeline_view import TimelineView

    class Main(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Kolnoa Editor — essai (Qt)")
            self.resize(1500, 950)
            self.sess = sess
            self.peaks = PeakService(cache_dir=work / "peaks")
            self.mc = MulticamView(lambda mk: MulticamPlayer(sess.tl, "g001", "V1", media, mk, budget=6e8, hw_decode=True))
            self.tlv = TimelineView(sess, peak_service=self.peaks, source_len=lambda m: next((d for a, d in durations.items()
                                                                                     if media[a].source == m), None))
            self.bin = BinView(sess)
            self.status = QLabel("1-9 : angle · Espace : lecture · Maj+Espace : enregistrer une prise · Entrée : valider · S : rasoir · Alt+glisser : ripple")
            self.stats = QLabel("")
            self.tlv.edited.connect(self._edited)
            self.tlv.rejected.connect(lambda m: self.status.setText("Refusé : " + m))
            self.mc.committed.connect(self._take)
            self.mc.refused.connect(lambda m: self.status.setText("Refusé : " + m))
            self.tlv.playheadChanged.connect(lambda f: self.mc.player.seek(f / sess.tl.fps))
            top = QSplitter(Qt.Horizontal)
            top.addWidget(self.bin)
            top.addWidget(self.mc)
            top.setStretchFactor(1, 5)
            bar = QHBoxLayout()
            for text, fn in (("Lecture / Pause", self._toggle), ("Annuler", self.tlv.undo), ("Rétablir", self.tlv.redo),
                             ("Mesurer (4K50, GPU)…", self._measure)):
                b = QPushButton(text)
                b.clicked.connect(fn)
                b.setFocusPolicy(Qt.NoFocus)
                bar.addWidget(b)
            bar.addStretch(1)
            bar.addWidget(self.stats)
            lay = QVBoxLayout()
            lay.addLayout(bar)
            split = QSplitter(Qt.Vertical)
            split.addWidget(top)
            split.addWidget(self.tlv)
            split.setStretchFactor(0, 3)
            lay.addWidget(split, 1)
            lay.addWidget(self.status)
            w = QWidget()
            w.setLayout(lay)
            self.setCentralWidget(w)
            from PySide6.QtCore import QTimer
            self._t = QTimer(self, interval=500)
            self._t.timeout.connect(self._tick)
            self._t.start()

        def _toggle(self):
            p = self.mc.player
            p.pause() if p.clock.playing else p.play()
            self.mc.setFocus()

        def _take(self, tx):
            try:
                self.sess.do(tx, "prise multicam")
                self.tlv.refresh_model()
                self.tlv.update()
                self.status.setText("Prise validée (annulable en un geste).")
            except Exception as e:
                self.status.setText("Refusé : " + str(e))

        def _edited(self, label):
            self.bin.refresh()
            self.status.setText(label)

        def _tick(self):
            pl = self.mc.player
            st = pl.stats
            lat = sorted(st.switch_latencies)[-20:]
            self.stats.setText(f"angle affiché : {pl.shown} · bascule médiane {1000 * lat[len(lat) // 2]:.0f} ms · "
                               f"re-seeks {st.reseeks} · dépassements de budget {st.over_budget_replans}" if lat else
                               f"angle affiché : {pl.shown} · {'⚠ ' + pl.warnings[0] if pl.warnings else 'budget OK'}")
            self.tlv.set_playhead(pl.frame(), emit=False) if pl.clock.playing else None

        def _measure(self):
            from PySide6.QtWidgets import QMessageBox
            files = [m.source for m in media.values()]
            out = work / "mesures"
            self.status.setText("Mesures en cours (quelques minutes, ne touche à rien)…")
            self.mc.player.pause()
            QApplication.processEvents()
            from ..player.suite import run_suite
            res = run_suite(files, out, log=lambda s: (self.status.setText(s), QApplication.processEvents()))
            self.status.setText(f"Mesures terminées → {out}")
            QMessageBox.information(self, "Mesures", f"{res['verdict_gpu']}\n\nRapport : {out / 'MESURES.md'}\n\nEnvoie mesures.json (et MESURES.md).")

        def closeEvent(self, e):
            self.mc.close()
            self.peaks.close()
            super().closeEvent(e)

    configure_hw()
    return Main()


def run_gui(argv: list[str]) -> int:
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog
    from ..player.qt_backend import configure_hw
    configure_hw()
    app = QApplication.instance() or QApplication(argv)
    videos = [a for a in argv[1:] if not a.startswith("-") and Path(a).is_file()]
    if not videos:
        videos, _ = QFileDialog.getOpenFileNames(None, "Choisis 2 à 9 vidéos (caméras d'un même événement)", str(Path.home()),
                                                 "Vidéos (*.mp4 *.mov *.mxf *.mkv *.m4v);;Tous (*.*)")
    if not videos:
        return 0
    work = default_work_dir()
    dlg = QProgressDialog("Préparation des proxys (début des rushes)…", "Annuler", 0, 100)
    dlg.setWindowTitle("Kolnoa — préparation")
    dlg.setMinimumDuration(0)
    box = {"cancel": False}
    dlg.canceled.connect(lambda: box.__setitem__("cancel", True))
    res: dict = {}

    def job():
        try:
            res["out"] = build_demo(videos, work, True, log=lambda s: res.__setitem__("msg", s),
                                    progress=lambda f: res.__setitem__("p", f), cancel=lambda: box["cancel"])
        except Exception as e:                               # noqa: BLE001 — on l'affiche à l'utilisateur
            res["err"] = str(e)

    th = threading.Thread(target=job, daemon=True)
    th.start()
    while th.is_alive():
        app.processEvents()
        dlg.setLabelText(res.get("msg", "Analyse des vidéos…"))
        dlg.setValue(int(100 * res.get("p", 0)))
        time.sleep(0.05)
    dlg.close()
    if "err" in res or box["cancel"]:
        if "err" in res:
            QMessageBox.critical(None, "Kolnoa", f"Impossible de préparer l'essai :\n{res['err']}")
        return 1
    sess, media, durations = res["out"]
    win = _make_window(sess, media, durations, work)
    win.show()
    return app.exec()
