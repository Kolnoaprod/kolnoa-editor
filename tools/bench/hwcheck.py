"""Vérification du décodage matériel sur la machine de montage (Windows, RTX A4500).  python hwcheck.py rush_4k50.mp4

Ne modifie rien. Étapes :
 1. FFmpeg : API matérielles compilées (cuda, d3d11va, dxva2…) et décodeurs NVDEC (h264_cuvid, hevc_cuvid).
 2. Décodage de TON rush par FFmpeg, logiciel puis matériel : vitesse (× temps réel) et utilisation du moteur de décodage GPU
    (nvidia-smi, colonne « dec ») — c'est la preuve que le GPU décode vraiment.
 3. Qt : lecture du rush par QMediaPlayer pendant 6 s avec/sans QT_FFMPEG_DECODING_HW_DEVICE_TYPES, avec le même échantillonnage GPU.
Résultat dans hwcheck.json (à joindre à la PR ; pas de nom de fichier client : mets un nom neutre).
"""

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

NV = shutil.which("nvidia-smi")


def sh(cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def gpu_sampler(stop, out):
    """Échantillonne `nvidia-smi dmon -s u` (sm, mem, enc, dec en %) toutes les secondes."""
    if not NV:
        return
    p = subprocess.Popen([NV, "dmon", "-s", "u", "-d", "1"], stdout=subprocess.PIPE, text=True)
    while not stop.is_set():
        line = p.stdout.readline()
        m = re.match(r"\s*\d+\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", line)
        if m:
            out.append({"sm": int(m[1]), "mem": int(m[2]), "enc": int(m[3]), "dec": int(m[4])})
    p.kill()


def with_gpu(fn):
    stop, samples = threading.Event(), []
    th = threading.Thread(target=gpu_sampler, args=(stop, samples), daemon=True)
    th.start()
    time.sleep(1.5)
    t0 = time.perf_counter()
    r = fn()
    el = time.perf_counter() - t0
    time.sleep(1.2)
    stop.set()
    dec = [s["dec"] for s in samples]
    return r, el, {"dec_max_pct": max(dec) if dec else None, "dec_moyen_pct": round(sum(dec) / len(dec), 1) if dec else None,
                   "echantillons": len(dec)}


def main(path):
    res = {"nvidia_smi": bool(NV)}
    out = sh(["ffmpeg", "-hide_banner", "-hwaccels"]).stdout
    res["ffmpeg_hwaccels"] = [l.strip() for l in out.splitlines()[1:] if l.strip()]
    dec = sh(["ffmpeg", "-hide_banner", "-decoders"]).stdout
    res["decodeurs_nvdec"] = sorted(set(re.findall(r"\b(h264_cuvid|hevc_cuvid)\b", dec)))
    dur = float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).stdout.strip() or 0)
    clip = min(dur, 20.0)
    res["ffmpeg"] = {}
    for name, args in (("logiciel", []), ("cuda", ["-hwaccel", "cuda"]), ("d3d11va", ["-hwaccel", "d3d11va"])):
        if args and args[1] not in res["ffmpeg_hwaccels"]:
            continue
        _, el, gpu = with_gpu(lambda a=args: sh(["ffmpeg", "-v", "error", *a, "-t", str(clip), "-i", path, "-an", "-f", "null", "-"]))
        res["ffmpeg"][name] = {"x_temps_reel": round(clip / el, 2), **gpu}
        print("ffmpeg", name, res["ffmpeg"][name])
    res["qt"] = {}
    for label, hw in (("logiciel", ""), ("cuda,d3d11va", "cuda,d3d11va"), ("d3d11va", "d3d11va")):
        code = ("import os,sys,time;sys.path.insert(0,r'%s');" % (Path(__file__).resolve().parents[2] / "src") +
                "from PySide6.QtWidgets import QApplication;a=QApplication([]);"
                "from kolnoa_editor.player.qt_backend import QtAngleBackend;"
                "from PySide6.QtCore import QEventLoop,QTimer;b=QtAngleBackend();b.open(sys.argv[1],'source');b.play(1.0);"
                "l=QEventLoop();QTimer.singleShot(6500,l.quit);l.exec();print(b.frames/6.5)")
        env = dict(__import__("os").environ, QT_MEDIA_BACKEND="ffmpeg")
        if hw:
            env["QT_FFMPEG_DECODING_HW_DEVICE_TYPES"] = hw
        else:
            env["QT_FFMPEG_DECODING_HW_DEVICE_TYPES"] = ""
        (r, el, gpu) = with_gpu(lambda: subprocess.run([sys.executable, "-c", code, path], capture_output=True, text=True, env=env, timeout=120))
        try:
            fps = round(float(r.stdout.strip().splitlines()[-1]), 1)
        except Exception:
            fps = None
        res["qt"][label] = {"images_par_s": fps, **gpu}
        print("qt", label, res["qt"][label])
    gpu_dec = [v["dec_max_pct"] or 0 for v in res["qt"].values()]
    res["conclusion"] = ("Qt utilise le moteur de décodage GPU" if max(gpu_dec[1:] or [0]) > max(gpu_dec[0], 5)
                         else "PAS de preuve que Qt décode sur le GPU (dec ≈ logiciel) — voir README")
    Path("hwcheck.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(res["conclusion"])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    main(sys.argv[1])
