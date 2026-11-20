"""Bancs du lecteur multicam — TROIS mesures séparées (ne mélangent jamais seek, décodage et affichage).

  python -m kolnoa_editor.player.bench gen   DOSSIER            génère 3 rushes synthétiques 4K50 (10 s) — ou donne tes vrais rushes
  python -m kolnoa_editor.player.bench seek    rush1 [rush2..]  latence seek → première image prête (QMediaPlayer), par lecteur isolé
  python -m kolnoa_editor.player.bench decode  rush1 [rush2..]  débit de décodage (images/s) lecture à 4×, N lecteurs, sans afficher
  python -m kolnoa_editor.player.bench display rush1 [rush2..]  coût d'affichage : conversion + peinture (ms), N flux
  python -m kolnoa_editor.player.bench all     rush1 [rush2..]  les trois, résultats dans bench_player.json

Option : --hw cuda,d3d11va   (autorise ces API matérielles, backend FFmpeg de Qt)    --out fichier.json
Les chiffres du bac à sable Linux (logiciel, sans GPU) ne valent PAS pour la RTX A4500 : à lancer sur le PC de montage.
(`tools/bench/bench_player.py` est le même programme.)
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from kolnoa_editor.ffmpeg import run_hidden


def pct(a, p):
    a = sorted(a)
    return round(a[min(len(a) - 1, int(len(a) * p))], 2) if a else None


def gen(folder: str, n: int = 3, seconds: int = 10, w: int = 3840, h: int = 2160, fps: int = 50) -> list[str]:
    out = []
    Path(folder).mkdir(parents=True, exist_ok=True)
    for i in range(n):
        f = str(Path(folder) / f"synthetic_4k50_{i + 1}.mp4")
        run_hidden(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                        f"testsrc2=size={w}x{h}:rate={fps}:duration={seconds}", "-f", "lavfi", "-i", f"sine=f={220 * (i + 1)}:duration={seconds}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p", "-g", "25",
                        "-c:a", "aac", "-shortest", f], check=True)
        out.append(f)
    return out


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _wait(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def seek(paths, n=30):
    """Latence : setPosition() → première image dont l'horodatage est proche. Un lecteur isolé à la fois."""
    from .qt_backend import QtAngleBackend
    _app()
    res = {}
    for idx, p in enumerate(paths, 1):
        be = QtAngleBackend()
        be.open(p, "source")
        _wait(500)
        lat, fail = [], 0
        for i in range(n):
            target = 1.0 + (i * 0.37) % 6.0
            be._frame_t = -1.0
            t0 = time.perf_counter()
            be.seek(target)
            while time.perf_counter() - t0 < 2.0:
                _app().processEvents()
                if be.has_frame_near(target, 0.1):
                    break
            else:
                fail += 1
                if fail >= 3 and not lat:          # fichier illisible : inutile d'insister 30 fois
                    break
                continue
            lat.append((time.perf_counter() - t0) * 1000)
        be.close()
        res[f"rush {idx}"] = {"n": len(lat), "echecs": fail, "mediane_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "max_ms": pct(lat, 1)}
    return res


def decode(paths, seconds=6.0, rate=4.0):
    """Débit : N lecteurs lus à `rate`× (sans écran) → images décodées par seconde de mur. Au-dessus de la cadence du rush
    (50 i/s pour du 4K50), la machine a de la marge ; en dessous de 50 × N, elle ne tient pas N flux en temps réel."""
    from .qt_backend import QtAngleBackend
    _app()
    out = {}
    for n in range(1, len(paths) + 1):
        bes = [QtAngleBackend() for _ in range(n)]
        for be, p in zip(bes, paths):
            be.open(p, "source")
        _wait(400)
        for be in bes:
            be.frames = 0
            be.play(rate)
        t0 = time.perf_counter()
        _wait(int(seconds * 1000))
        el = time.perf_counter() - t0
        out[f"{n}_flux"] = {"vitesse": rate, "images_par_s": [round(be.frames / el, 1) for be in bes]}
        for be in bes:
            be.close()
    return out


def display(paths, seconds=5.0):
    """Affichage : temps passé à PEINDRE les images reçues (conversion + dessin du widget), pour N flux affichés."""
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QWidget
    from .qt_backend import QtAngleBackend

    app = _app()
    out = {}

    class V(QWidget):
        def __init__(self):
            super().__init__()
            self.img = None
            self.ms = []
            self.pending = False
            self.skipped = 0
            self.resize(480, 270)

        def paintEvent(self, e):
            t0 = time.perf_counter()
            self.pending = False
            if self.img is not None:
                p = QPainter(self)
                p.drawImage(self.rect(), self.img)
                p.end()
            self.ms.append((time.perf_counter() - t0) * 1000)

    for n in range(1, len(paths) + 1):
        views = [V() for _ in range(n)]
        conv = []

        def on_frame(be, frame, v=None):
            t0 = time.perf_counter()
            v = views[bes.index(be)]
            if v.pending:          # la peinture précédente n'est pas finie : on saute l'image (comme un vrai lecteur)
                v.skipped += 1
                return
            v.pending = True
            v.img = frame.toImage()
            conv.append((time.perf_counter() - t0) * 1000)
            v.update()

        bes = [QtAngleBackend(on_frame) for _ in range(n)]
        for v in views:
            v.show()
        for be, p in zip(bes, paths):
            be.open(p, "source")
            be.play(1.0)
        _wait(int(seconds * 1000))
        allp = [x for v in views for x in v.ms[3:]]
        out[f"{n}_flux"] = {"conversion_image_ms": {"mediane": pct(conv, .5), "p95": pct(conv, .95)},
                            "peinture_ms": {"mediane": pct(allp, .5), "p95": pct(allp, .95), "n": len(allp)},
                            "images_sautees": sum(v.skipped for v in views)}
        for be in bes:
            be.close()
        for v in views:
            v.close()
    return out


def main(argv):
    hw = None
    out = "bench_player.json"
    args = []
    it = iter(argv)
    for a in it:
        if a == "--hw":
            hw = next(it)
        elif a == "--out":
            out = next(it)
        else:
            args.append(a)
    if not args:
        print(__doc__)
        return 2
    cmd, rest = args[0], args[1:]
    if cmd == "gen":
        print("\n".join(gen(rest[0] if rest else "synthetic")))
        return 0
    from .qt_backend import configure_hw
    configure_hw(None if hw in (None, "none") else hw)
    if not rest:
        print("donne au moins un fichier vidéo")
        return 2
    res = {"hw": hw or "logiciel"}
    for name, fn in (("seek", seek), ("decode", decode), ("display", display)):
        if cmd in (name, "all"):
            res[name] = fn(rest)
            print(name, json.dumps(res[name], indent=1))
    Path(out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
