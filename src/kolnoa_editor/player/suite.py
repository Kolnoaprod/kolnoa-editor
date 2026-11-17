"""Série de mesures du lecteur sur les VRAIS rushes (4K50…) : seek, décodage, affichage, avec et sans décodage matériel,
+ lecture du moteur de décodage GPU (`nvidia-smi dmon`, colonne `dec`) pendant chaque mesure.

Chaque mesure tourne dans un processus séparé (les variables d'environnement Qt doivent être posées avant son démarrage).
Ne modifie jamais les rushes. Écrit `mesures.json` et `MESURES.md` (français, sans nom de fichier : « rush 1 », « rush 2 »…).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

CONFIGS = [("logiciel", ""), ("gpu (cuda, d3d11va)", "cuda,d3d11va")]
MODES = ("seek", "decode", "display")
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def worker_cmd() -> list[str]:
    """Commande qui lance UNE mesure : dans l'EXE, l'EXE se relance lui-même ; sinon `python -m kolnoa_editor.player.bench`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--player-worker"]
    return [sys.executable, "-m", "kolnoa_editor.player.bench"]


def _sample_gpu(stop: threading.Event, out: list) -> None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return
    try:
        p = subprocess.Popen([exe, "dmon", "-s", "u", "-d", "1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, text=True, creationflags=NO_WINDOW)
    except OSError:
        return
    while not stop.is_set():
        line = p.stdout.readline()
        m = re.match(r"\s*\d+\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", line or "")
        if m:
            out.append({"sm": int(m[1]), "mem": int(m[2]), "enc": int(m[3]), "dec": int(m[4])})
        elif not line:
            break
    p.kill()


def run_one(mode: str, hw: str, files: list[str], timeout: float = 900.0, cancel: Callable[[], bool] | None = None) -> dict:
    """Une mesure (processus fils) + échantillonnage GPU. Renvoie {'resultat': ..., 'gpu': {...}} ou {'erreur': ...}."""
    out = Path(os.environ.get("TEMP", "/tmp")) / f"kolnoa_mesure_{os.getpid()}_{mode}.json"
    env = dict(os.environ, QT_MEDIA_BACKEND="ffmpeg", QT_FFMPEG_DECODING_HW_DEVICE_TYPES=hw)
    if not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    stop, samples = threading.Event(), []
    th = threading.Thread(target=_sample_gpu, args=(stop, samples), daemon=True)
    th.start()
    time.sleep(1.2)                                      # niveau de repos avant la mesure
    t0 = time.time()
    try:
        p = subprocess.Popen(worker_cmd() + [mode, *files, "--hw", hw or "none", "--out", str(out)], env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        while p.poll() is None:
            if (cancel and cancel()) or time.time() - t0 > timeout:
                p.kill()
                p.wait()
                return {"erreur": "annulé" if cancel and cancel() else "délai dépassé"}
            time.sleep(0.2)
    except OSError as e:
        return {"erreur": str(e)}
    finally:
        time.sleep(1.1)
        stop.set()
    dec = [s["dec"] for s in samples]
    gpu = {"echantillons": len(dec), "dec_max_pct": max(dec) if dec else None,
           "dec_moyen_pct": round(sum(dec) / len(dec), 1) if dec else None,
           "sm_max_pct": max((s["sm"] for s in samples), default=None)}
    try:
        res = json.loads(out.read_text(encoding="utf-8"))
        out.unlink(missing_ok=True)
    except (OSError, ValueError):
        return {"erreur": f"mesure sans résultat (code {p.returncode})", "gpu": gpu}
    return {"resultat": res.get(mode), "gpu": gpu}


def run_suite(files: list[str], out_dir: str | Path, log: Callable[[str], None] = print,
              progress: Callable[[float], None] = lambda f: None, cancel: Callable[[], bool] | None = None,
              configs=CONFIGS, modes=MODES) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res: dict = {"date": time.strftime("%Y-%m-%d %H:%M"), "n_rushes": len(files), "nvidia_smi": bool(shutil.which("nvidia-smi")),
                 "mesures": {}}
    total, done = len(configs) * len(modes), 0
    for label, hw in configs:
        res["mesures"][label] = {}
        for mode in modes:
            if cancel and cancel():
                res["annule"] = True
                break
            log(f"Mesure « {mode} » — {label}…")
            res["mesures"][label][mode] = run_one(mode, hw, files, cancel=cancel)
            done += 1
            progress(done / total)
    res["verdict_gpu"] = verdict(res)
    (out_dir / "mesures.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "MESURES.md").write_text(render(res), encoding="utf-8")
    return res


def verdict(res: dict) -> str:
    m = res["mesures"]
    base = [v.get("gpu", {}).get("dec_max_pct") or 0 for k, c in m.items() if k.startswith("logiciel") for v in c.values()]
    gpu = [v.get("gpu", {}).get("dec_max_pct") or 0 for k, c in m.items() if k.startswith("gpu") for v in c.values()]
    if not res.get("nvidia_smi"):
        return "nvidia-smi absent : impossible de prouver le décodage GPU (comparer les vitesses uniquement)."
    if max(gpu or [0]) >= 10 and max(gpu or [0]) > max(base or [0]) + 5:
        return f"Le décodage matériel semble ACTIF (moteur de décodage GPU jusqu'à {max(gpu)} %, contre {max(base or [0])} % en logiciel)."
    return "PAS de preuve que Qt décode sur le GPU (moteur de décodage ~inactif) : prévoir le plan B (décodeur NVDEC dédié)."


def _fmt(d) -> str:
    return json.dumps(d, ensure_ascii=False)


def render(res: dict) -> str:
    L = ["# Mesures du lecteur multicam", "", f"Date : {res['date']} · {res['n_rushes']} rush(es) · nvidia-smi : {'oui' if res['nvidia_smi'] else 'non'}", "",
         f"**{res['verdict_gpu']}**", ""]
    for label, modes in res["mesures"].items():
        L += [f"## {label}", ""]
        for mode, r in modes.items():
            L.append(f"### {mode}")
            if "erreur" in r:
                L += [f"Erreur : {r['erreur']}", ""]
                continue
            for name, v in (r["resultat"] or {}).items():
                L.append(f"- {name} : `{_fmt(v)}`")
            g = r["gpu"]
            L += [f"- moteur de décodage GPU : max {g['dec_max_pct']} %, moyen {g['dec_moyen_pct']} % ({g['echantillons']} échantillons)", ""]
    L += ["---", "Les noms de fichiers ne sont pas écrits ici (dépôt public). À joindre à la PR : `mesures.json` et ce fichier."]
    return "\n".join(L) + "\n"
