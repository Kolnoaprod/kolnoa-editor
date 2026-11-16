"""Harnais de l'essai Qt / web local : prépare les mêmes médias et la même timeline, lance chaque prototype, mesure CPU/RAM/GPU.

    python tools\\bench\\bench_run.py --media cam1.mp4 cam2.mp4 cam3.mp4 --out C:\\kolnoa_bench

Ne modifie JAMAIS les médias : proxys, pics et résultats sont écrits dans le dossier --out. Rien n'est envoyé hors de la machine
(le serveur web écoute 127.0.0.1 uniquement).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import bench_common as bc

FROZEN = bool(getattr(sys, "frozen", False))
HERE = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)     # EXE : dossier d'extraction PyInstaller


QUIET = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # EXE fenêtré : pas de std valides


def use_bundled_ffmpeg() -> None:
    """Dans l'EXE, ffmpeg.exe / ffprobe.exe sont embarqués à côté des ressources : on les met en tête du PATH."""
    if (HERE / "bin").is_dir():
        os.environ["PATH"] = str(HERE / "bin") + os.pathsep + os.environ.get("PATH", "")


# --- machine ----------------------------------------------------------------------------------------
def _run(cmd: list[str]) -> str | None:
    try:
        return bc.run_capture(cmd, timeout=20).decode("utf-8", "replace").strip() or None
    except Exception:
        return None


def machine_info(browser: str | None) -> dict:
    import psutil
    info = {"os": f"{platform.system()} {platform.release()} ({platform.version()})", "cpu": platform.processor() or None,
            "coeurs": f"{psutil.cpu_count(logical=False)} physiques / {psutil.cpu_count()} logiques",
            "ram_go": round(psutil.virtual_memory().total / 2**30, 1), "python": sys.version.split()[0]}
    if platform.system() == "Windows":
        name = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"])
        gpu = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name + ' ' + $_.DriverVersion }) -join '; '"])
        info["cpu"], info["gpu"] = name or info["cpu"], gpu
    else:
        info["gpu"] = _run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    ff = _run(["ffmpeg", "-version"])
    info["ffmpeg"] = ff.splitlines()[0] if ff else None
    info["navigateur"] = browser
    try:
        import PySide6
        info["pyside6"] = PySide6.__version__
    except ImportError:
        info["pyside6"] = None
    return info


# --- échantillonnage ressources ----------------------------------------------------------------------
class Sampler(threading.Thread):
    """Toutes les 0,5 s : CPU (%, somme des processus suivis) et mémoire. GPU via nvidia-smi si présent."""

    def __init__(self, pid_finder):
        super().__init__(daemon=True)
        self.find, self.samples, self.stop_flag = pid_finder, [], threading.Event()
        self.has_nvsmi = shutil.which("nvidia-smi") is not None
        self._procs: dict = {}

    def _gpu(self):
        if not self.has_nvsmi:
            return None
        out = _run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"])
        try:
            return float(out.splitlines()[0]) if out else None
        except ValueError:
            return None

    def run(self):
        import psutil
        n = 0
        while not self.stop_flag.wait(0.5):
            cpu, rss = 0.0, 0
            alive = {}
            for pid in self.find():
                try:
                    p = self._procs.get(pid) or psutil.Process(pid)
                    cpu += p.cpu_percent(None)        # 100 % = un cœur ; la première lecture d'un processus vaut 0
                    rss += p.memory_info().rss
                    alive[pid] = p
                except psutil.Error:
                    continue
            self._procs = alive
            n += 1
            gpu = self._gpu() if n % 2 == 0 else (self.samples[-1].get("gpu") if self.samples else None)
            self.samples.append({"t": time.time(), "cpu": round(cpu, 1), "rss_mo": rss / 2**20, "gpu": gpu})

    def stop(self):
        self.stop_flag.set()
        self.join(2)


def descendants(pid: int) -> list[int]:
    import psutil
    try:
        p = psutil.Process(pid)
        return [pid] + [c.pid for c in p.children(recursive=True)]
    except psutil.Error:
        return []


def pids_with_arg(token: str) -> list[int]:
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if any(token in a for a in (p.info["cmdline"] or [])):
                out.append(p.info["pid"])
        except psutil.Error:
            pass
    return out


# --- serveur local (Range indispensable pour que <video> puisse se déplacer) --------------------------
class Server:
    def __init__(self, files: dict[str, Path]):
        self.files, self.results = files, {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.results[self.path.strip("/")] = json.loads(body or b"{}")
                self.send_response(204)
                self.end_headers()

            def do_GET(self):
                f = outer.files.get(self.path.split("?")[0])
                if f is None or not f.is_file():
                    self.send_error(404)
                    return
                size = f.stat().st_size
                rng = self.headers.get("Range")
                start, end = 0, size - 1
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    start = int(a) if a else 0
                    end = int(b) if b else size - 1
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(str(f))[0] or "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                try:
                    with open(f, "rb") as fh:
                        fh.seek(start)
                        left = end - start + 1
                        while left > 0:
                            chunk = fh.read(min(1 << 20, left))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                except (ConnectionError, OSError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


# --- navigateur --------------------------------------------------------------------------------------
def find_browser(choice: str | None) -> str | None:
    if choice and Path(choice).exists():
        return choice
    cands = {"edge": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                      r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "msedge", "microsoft-edge"],
             "chrome": [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "google-chrome", "chromium", "chrome"]}
    for name in ([choice] if choice in cands else ["edge", "chrome"]):
        for c in cands[name]:
            if Path(c).exists() or shutil.which(c):
                return c
    return None


# --- exécution ---------------------------------------------------------------------------------------
def wait_for(cond, timeout: float, what: str):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.5)
    print(f"  ! délai dépassé : {what}")
    return False


def attach_resources(run: dict, samples: list[dict]) -> None:
    run["resources"] = {ph["name"]: bc.resource_summary(samples, ph["t0"], ph["t1"]) for ph in run.get("phases", [])}


def run_qt(out: Path, variant: str, timeout: float, no_audio: bool = False, cancel=None) -> dict:
    res_file = out / f"qt_{variant}.json"
    res_file.unlink(missing_ok=True)
    t_launch = time.time()
    worker = [sys.executable, "--qt-worker"] if FROZEN else [sys.executable, str(HERE / "bench_qt.py")]
    proc = subprocess.Popen([*worker, "--scenario", str(out / "scenario.json"),
                             "--media-map", str(out / "media_map.json"), "--peaks", str(out / "peaks.json"),
                             "--variant", variant, "--out", str(res_file), "--launch-epoch", str(t_launch),
                             *(["--no-audio"] if no_audio else [])], **QUIET)
    sampler = Sampler(lambda: descendants(proc.pid))
    sampler.start()
    ok = wait_for(lambda: proc.poll() is not None or (cancel and cancel()), timeout, f"Qt {variant}")
    if proc.poll() is None:
        proc.kill()
    sampler.stop()
    run = json.loads(res_file.read_text(encoding="utf-8")) if res_file.exists() else \
        {"toolkit": "qt", "variant": variant, "metrics": {}, "phases": [], "errors": ["aucun résultat écrit par le prototype Qt"]}
    attach_resources(run, sampler.samples)
    return run


def run_web(out: Path, variant: str, srv: Server, browser: str, timeout: float, extra: list[str] | None = None,
            cancel=None) -> dict:
    profile = Path(tempfile.mkdtemp(prefix="kolnoa_bench_profile_"))
    key = f"web_{variant}"
    srv.results.pop(key, None)
    url = f"http://127.0.0.1:{srv.port}/bench_web.html?variant={variant}&launch={time.time()}"
    proc = subprocess.Popen([browser, f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                             "--autoplay-policy=no-user-gesture-required", "--disable-extensions",
                             "--new-window", *(extra or []), url], **QUIET)
    sampler = Sampler(lambda: pids_with_arg(str(profile)))
    sampler.start()
    ok = wait_for(lambda: key in srv.results or (cancel and cancel()), timeout, f"web {variant}")
    sampler.stop()
    for pid in pids_with_arg(str(profile)):
        try:
            import psutil
            psutil.Process(pid).kill()
        except Exception:
            pass
    shutil.rmtree(profile, ignore_errors=True)
    run = srv.results.get(key) or {"toolkit": "web", "variant": variant, "metrics": {}, "phases": [],
                                   "errors": ["aucun résultat reçu du navigateur"]}
    attach_resources(run, sampler.samples)
    return run


class Cancelled(Exception):
    pass


def prepare(media: list[str | Path], out: Path, audio=None, play_seconds: float = 20.0, log=print) -> dict:
    """Sondes, proxys (mis en cache), pics, scénario. Retourne ce qu'il faut pour lancer les essais. Ne modifie jamais les sources."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "proxies").mkdir(exist_ok=True)
    srcs = [Path(m).resolve() for m in media][:9]
    for s_ in srcs:
        if not s_.is_file():
            raise FileNotFoundError(f"Fichier introuvable : {s_}")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg et ffprobe sont introuvables (ils devraient être inclus dans le programme).")
    log("Lecture des informations des vidéos…")
    infos = [bc.probe(s_) for s_ in srcs]
    angles = [{"id": f"C{i + 1}", "key": i + 1, "name": m["name"], "duration_s": m["duration_s"]} for i, m in enumerate(infos)]
    proxies = {}
    for ang, s_ in zip(angles, srcs):
        p = out / "proxies" / f"{ang['id']}.mp4"
        if not p.exists():
            log(f"Création de la copie légère de {s_.name} (une seule fois)…")
            bc.make_proxy(s_, p)
        proxies[ang["id"]] = p
    log("Formes d'onde et scénario…")
    peaks = bc.make_peaks(audio or srcs[0])
    scenario = bc.build_scenario(angles, play_s=play_seconds)
    (out / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
    (out / "peaks.json").write_text(json.dumps(peaks), encoding="utf-8")
    media_map = {"proxy": {k: str(v) for k, v in proxies.items()},
                 "source": {ang["id"]: str(s_) for ang, s_ in zip(angles, srcs)}}
    (out / "media_map.json").write_text(json.dumps(media_map), encoding="utf-8")
    return {"media": infos, "scenario": scenario, "media_map": media_map}


def make_server(out: Path, media_map: dict) -> Server:
    files = {"/bench_web.html": HERE / "bench_web.html", "/scenario.json": out / "scenario.json", "/peaks.json": out / "peaks.json"}
    for variant in ("proxy", "source"):
        for cid, path in media_map[variant].items():
            files[f"/media/{variant}/{cid}"] = Path(path)
    return Server(files)


def run_pipeline(media, out: Path, rounds: int = 1, variants=("proxy", "source"), only=None, browser_choice=None,
                 browser_args=None, no_audio=False, play_seconds=20.0, timeout=420.0, audio=None,
                 log=print, progress=lambda done, total, label: None, cancel=lambda: False) -> dict:
    """Prépare puis lance `rounds` tours de (Qt, web) × variantes. Retourne les résultats (un run par tour, avec la clé "round").
    `progress(done, total, label)` et `cancel()` servent à l'interface graphique."""
    prep = prepare(media, out, audio, play_seconds, log)
    browser = find_browser(browser_choice)
    results = {"machine": machine_info(browser), "media": prep["media"], "scenario": prep["scenario"], "runs": [],
               "rounds": rounds, "launched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    steps = [(r, v, tk) for r in range(1, rounds + 1) for v in variants for tk in ("qt", "web") if only in (None, tk)]
    total = len(steps)
    srv = make_server(out, prep["media_map"])
    try:
        for done, (r, variant, tk) in enumerate(steps):
            if cancel():
                raise Cancelled()
            label = f"Essai {r}/{rounds} · {'Qt' if tk == 'qt' else 'Web'} · {'copies légères' if variant == 'proxy' else 'vidéos originales'}"
            progress(done, total, label)
            log(label)
            if tk == "qt":
                if results["machine"]["pyside6"]:
                    run = run_qt(out, variant, timeout, no_audio, cancel)
                else:
                    run = {"toolkit": "qt", "variant": variant, "metrics": {}, "phases": [],
                           "errors": ["PySide6 non disponible"]}
            elif browser:
                run = run_web(out, variant, srv, browser, timeout, browser_args, cancel)
            else:
                run = {"toolkit": "web", "variant": variant, "metrics": {}, "phases": [],
                       "errors": ["aucun navigateur Edge/Chrome trouvé"]}
            if cancel():
                raise Cancelled()
            run["round"] = r
            results["runs"].append(run)
        progress(total, total, "Terminé")
    finally:
        srv.close()
    return results


def write_outputs(results: dict, out: Path) -> Path:
    """results.json (brut) + REPORT.md (rapport agrégé si plusieurs tours)."""
    (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    report = out / "REPORT.md"
    report.write_text(bc.render_report(bc.aggregate_rounds(results)), encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--media", nargs="+", required=True, help="1 à 9 vidéos (une par caméra), mêmes fichiers pour Qt et web")
    ap.add_argument("--audio", help="enregistreur : sert aux formes d'onde (sinon l'audio de la 1re vidéo)")
    ap.add_argument("--out", default="kolnoa_bench", help="dossier de travail et de résultats")
    ap.add_argument("--only", choices=["qt", "web"])
    ap.add_argument("--variants", nargs="+", default=["proxy", "source"], choices=["proxy", "source"])
    ap.add_argument("--browser", help="edge | chrome | chemin de l'exécutable")
    ap.add_argument("--browser-arg", action="append", default=[], help="argument supplémentaire pour le navigateur (répétable)")
    ap.add_argument("--no-audio", action="store_true", help="machine sans carte son : désactive la sortie audio du prototype Qt")
    ap.add_argument("--play-seconds", type=float, default=20.0)
    ap.add_argument("--timeout", type=float, default=420.0, help="secondes max par exécution")
    ap.add_argument("--rounds", type=int, default=1, help="nombre de tours complets (3 recommandé)")
    ap.add_argument("--dry-run", action="store_true", help="prépare tout (proxys, pics, scénario) sans lancer l'interface")
    a = ap.parse_args()

    out = Path(a.out).resolve()
    if a.dry_run:
        prepare(a.media, out, a.audio, a.play_seconds)
        print("Préparation terminée (--dry-run) :", out)
        return
    results = run_pipeline(a.media, out, a.rounds, tuple(a.variants), a.only, a.browser, a.browser_arg, a.no_audio,
                           a.play_seconds, a.timeout, a.audio, progress=lambda d, t, l: print(f"[{d}/{t}] {l}"))
    print(f"Terminé. Envoie le fichier : {write_outputs(results, out)} (et results.json).")


if __name__ == "__main__":
    use_bundled_ffmpeg()
    main()
