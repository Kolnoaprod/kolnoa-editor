"""Briques communes du protocole d'essai Qt / web local : sondes média, scénario partagé, statistiques, rapport.

Tout est déterministe (graine fixe) pour que Qt et web rejouent EXACTEMENT la même timeline et les mêmes gestes.
Rien n'est inventé : une mesure absente s'affiche « non mesuré » dans le rapport.
"""

from __future__ import annotations

import json
import math
import random
import os
import subprocess
import tempfile
from pathlib import Path

PEAK_BIN_S = 0.02            # un pic d'amplitude par 20 ms
PEAK_MAX_S = 600             # au plus 10 minutes de pics (la forme d'onde est réutilisée sur toutes les pistes)


def run_capture(cmd: list[str], timeout: float | None = None, check: bool = True) -> bytes:
    """Lance une commande SANS tube ni console : sortie et erreurs vont dans des fichiers temporaires, stdin fermé.
    Robuste dans un EXE Windows « fenêtré » (pas de stdin/stdout valides) ; l'erreur du programme est reprise dans l'exception."""
    with tempfile.TemporaryFile() as fo, tempfile.TemporaryFile() as fe:
        try:
            rc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=fo, stderr=fe, timeout=timeout).returncode
        except FileNotFoundError:
            raise RuntimeError(f"programme introuvable : {cmd[0]}") from None
        fo.seek(0)
        fe.seek(0)
        out, err = fo.read(), fe.read().decode("utf-8", "replace").strip()
    if check and rc != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} a échoué (code {rc}) : {err[-600:] or 'sans message'}")
    return out


# --- statistiques ------------------------------------------------------------------------------------
def summarize(values: list[float] | None) -> dict | None:
    """Médiane / p95 / max sur une liste de mesures (millisecondes, ou autre unité cohérente). Liste vide : None."""
    v = sorted(x for x in (values or []) if x is not None and not math.isnan(x))
    if not v:
        return None
    def q(p):
        return v[min(len(v) - 1, int(len(v) * p))]
    return {"n": len(v), "mediane": round(q(.5), 2), "p95": round(q(.95), 2), "max": round(v[-1], 2)}


# --- sondes média -----------------------------------------------------------------------------------
def probe(path: str | Path) -> dict:
    """ffprobe : codec, résolution, fréquence d'images, durée, audio. Ne lit pas le média en entier."""
    out = run_capture(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    d = json.loads(out.decode("utf-8", "replace"))
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    info = {"path": str(path), "name": Path(path).name, "duration_s": float(d["format"].get("duration", 0) or 0),
            "has_video": v is not None, "has_audio": a is not None}
    if v:
        num, _, den = (v.get("avg_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        info.update(codec=v.get("codec_name"), width=v.get("width"), height=v.get("height"), fps=round(fps, 3),
                    pix_fmt=v.get("pix_fmt"), profile=v.get("profile"))
    if a:
        info.update(audio_codec=a.get("codec_name"), audio_rate=int(a.get("sample_rate", 0) or 0),
                    audio_channels=a.get("channels"))
    return info


PROXY_MAX_S = 90          # le test n'a besoin que de 90 s par caméra : on n'encode pas des rushes entiers


def make_proxy(src: str | Path, dst: str | Path, height: int = 540, max_s: float | None = PROXY_MAX_S) -> None:
    """Proxy de lecture fluide : H.264 basse résolution, une image clé par seconde, audio AAC. N'écrase jamais la source."""
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise ValueError("le proxy ne peut pas écraser la source")
    fps = probe(src).get("fps") or 25
    run_capture(["ffmpeg", "-y", "-v", "error", *(["-t", str(max_s)] if max_s else []), "-i", str(src), "-vf", f"scale=-2:{height}", "-c:v", "libx264",
                 "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-g", str(max(1, round(fps))),
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)])


def make_peaks(src: str | Path) -> list[int]:
    """Pics d'amplitude 0..255 par tranche de 20 ms (mono 8 kHz). Sert aux formes d'onde des deux prototypes."""
    import numpy as np
    raw = run_capture(["ffmpeg", "-v", "error", "-i", str(src), "-t", str(PEAK_MAX_S), "-vn", "-ac", "1", "-ar", "8000",
                       "-f", "f32le", "-"])
    x = np.frombuffer(raw, dtype=np.float32)
    per = int(8000 * PEAK_BIN_S)
    n = len(x) // per
    if n == 0:
        return []
    peaks = np.abs(x[: n * per]).reshape(n, per).max(axis=1)
    return [int(min(255, round(float(p) * 255))) for p in peaks]


# --- scénario partagé -------------------------------------------------------------------------------
def build_scenario(angles: list[dict], seed: int = 20261116, play_s: float = 20.0, n_clips: int = 800,
                   n_audio_tracks: int = 8, n_video_tracks: int = 2) -> dict:
    """Timeline virtuelle + gestes rejoués à l'identique par Qt et web.

    `angles` : [{id, key, duration_s}], jusqu'à 9. Les durées servent à borner la lecture et les scrubs."""
    if not angles:
        raise ValueError("au moins un angle vidéo est nécessaire")
    rnd = random.Random(seed)
    fps = 25
    usable = max(2.0, min(a["duration_s"] for a in angles) - 1.0)
    play_s = min(play_s, usable)
    n_tracks = n_video_tracks + n_audio_tracks
    horizon = 2 * 3600 * fps                              # 2 h de film virtuel : même ordre de grandeur qu'un mariage brut
    clips = []
    for i in range(n_clips):
        t = rnd.randrange(n_tracks)
        start = rnd.randrange(0, horizon)
        clips.append({"id": f"s{i + 1:04d}", "track": t, "start": start, "dur": rnd.randrange(25, 25 * 40)})
    keys = [a["key"] for a in angles]
    events, t = [], 1.0
    while t < play_s - 1.0:
        events.append({"t": round(t, 3), "key": rnd.choice(keys)})
        t += rnd.uniform(1.0, 2.5)
    return {
        "seed": seed, "fps": fps, "play_s": play_s, "angles": angles,
        "tracks": [{"id": f"V{i + 1}", "kind": "video"} for i in range(n_video_tracks)] +
                  [{"id": f"A{i + 1}", "kind": "audio"} for i in range(n_audio_tracks)],
        "clips": clips,
        "angle_events": events,                           # touches pressées pendant la lecture (t = seconde de lecture)
        "scrubs": [round(rnd.uniform(0.5, usable), 3) for _ in range(40)],
        "play_pause": 20,                                 # nombre d'allers-retours lecture/pause
        "pan_zoom_frames": 300,
        "drags": [{"x0": rnd.randrange(50, 400), "y0": rnd.randrange(20, 100),
                   "dx": rnd.randrange(200, 900), "dy": rnd.randrange(20, 200)} for _ in range(30)],
    }


# --- rapport -----------------------------------------------------------------------------------------
NM = "non mesuré"
METRICS = [("startup_s", "Lancement jusqu'à la 1re image (s)"),
           ("decode_only", "Décodage seul, sans interface (images/s présentées)"),
           ("play_pause_ms", "Lecture / pause : latence (ms)"),
           ("scrub_ms", "Scrub : délai jusqu'à l'image (ms)"),
           ("angle_switch_parallel_ms", "Touche 1-9 en lecture, angles lus en parallèle (ms)"),
           ("angle_switch_seek_ms", "Touche 1-9, un seul lecteur qui change de source (ms)"),
           ("timeline_pan_zoom_ms", "Timeline 800 plans + 8 formes d'onde : redessin (ms)"),
           ("drag_io_ms", "Glisser un plan I/O vers la timeline : redessin (ms)"),
           ("ui_playback", "Lecture avec interface (images/s présentées, perdues)")]


def _fmt(v) -> str:
    if v is None:
        return NM
    if isinstance(v, dict) and "mediane" in v:
        return f"méd. {v['mediane']} · p95 {v['p95']} · max {v['max']} (n={v['n']})"
    if isinstance(v, dict):
        return " · ".join(f"{k} {x}" for k, x in v.items())
    return str(v)


def resource_summary(samples: list[dict], t0: float, t1: float) -> dict | None:
    """CPU (%, somme des processus) et mémoire (Mo) pendant [t0, t1] ; GPU si la machine l'a donné."""
    s = [x for x in samples if t0 <= x["t"] <= t1]
    if not s:
        return None
    gpu = [x["gpu"] for x in s if x.get("gpu") is not None]
    return {"cpu": summarize([x["cpu"] for x in s]), "ram_mo_max": round(max(x["rss_mo"] for x in s), 1),
            "gpu_pct": summarize(gpu) if gpu else None}


def render_report(results: dict) -> str:
    """Rapport Markdown. Les chiffres viennent tous de `results` ; ce qui manque est écrit « non mesuré »."""
    env = results.get("machine", {})
    lines = ["# Résultats de l'essai Qt / web local", "",
             "> Mesures brutes sur le PC de l'utilisateur. Aucun chiffre n'est estimé ni inventé ; « non mesuré » = l'essai n'a rien rendu.", "",
             "## Machine", ""]
    for k in ("os", "cpu", "coeurs", "ram_go", "gpu", "python", "ffmpeg", "navigateur", "pyside6"):
        lines.append(f"- {k} : {env.get(k) or NM}")
    lines += ["", "## Médias utilisés (identiques pour Qt et web)", ""]
    for m in results.get("media", []):
        lines.append(f"- {m.get('name')} : {m.get('codec', '?')} {m.get('width', '?')}×{m.get('height', '?')} "
                     f"{m.get('fps', '?')} i/s, {m.get('duration_s', 0):.0f} s, audio {m.get('audio_codec') or 'aucun'}")
    sc = results.get("scenario", {})
    lines += ["", f"Scénario : graine {sc.get('seed', NM)}, {len(sc.get('clips', []))} plans, {len(sc.get('tracks', []))} pistes, "
              f"{len(sc.get('angle_events', []))} touches d'angle, {len(sc.get('scrubs', []))} scrubs.", ""]
    for variant in ("proxy", "source"):
        runs = {r["toolkit"]: r for r in results.get("runs", []) if r.get("variant") == variant}
        lines += [f"## Lecture : {'proxys' if variant == 'proxy' else 'médias source'}", "",
                  "| Mesure | Qt (PySide6) | Web local |", "|---|---|---|"]
        for key, label in METRICS:
            cells = [_fmt((runs.get(tk) or {}).get("metrics", {}).get(key)) for tk in ("qt", "web")]
            lines.append(f"| {label} | {cells[0]} | {cells[1]} |")
        phases = []
        for tk in ("qt", "web"):
            for ph in ((runs.get(tk) or {}).get("resources") or {}):
                if ph not in phases:
                    phases.append(ph)
        for ph in phases:
            cells = [_fmt_res(((runs.get(tk) or {}).get("resources") or {}).get(ph)) for tk in ("qt", "web")]
            lines.append(f"| Ressources · {ph} | {cells[0]} | {cells[1]} |")
        errs = [f"{r['toolkit']}: {e}" for r in runs.values() for e in r.get("errors", [])]
        if errs:
            lines += ["", "Erreurs / avertissements :"] + [f"- {e}" for e in errs]
        lines.append("")
    detail = results.get("rounds_detail")
    if detail:
        lines += ["## Tours individuels", "", f"{len({r.get('round') for r in detail})} tours ; chiffres ci-dessus = médiane des tours (pire p95/max).", "",
                  "| Tour | Outil | Médias | Lancement (s) | Lecture/pause (ms, méd.) | Scrub (ms, méd.) | Erreurs |", "|---|---|---|---|---|---|---|"]
        for r in detail:
            m = r.get("metrics", {})
            g = lambda k: (m.get(k) or {}).get("mediane", NM) if isinstance(m.get(k), dict) else (m.get(k) if m.get(k) is not None else NM)
            lines.append(f"| {r.get('round')} | {r['toolkit']} | {r['variant']} | {g('startup_s')} | {g('play_pause_ms')} | {g('scrub_ms')} | {len(r.get('errors', []))} |")
        lines.append("")
    lines += ["## Limites", "",
              "- Une seule machine ; plusieurs tours dans une même session (voir plus haut), pas de comparaison entre machines.",
              "- CPU/RAM de Qt : processus Python ; du web : tous les processus du navigateur de l'essai (profil dédié).",
              "- Le GPU n'est lu que via `nvidia-smi` quand il existe.",
              "- Emballage Windows (taille, démarrage à froid, installeur) : à évaluer à part, voir le README.", ""]
    return "\n".join(lines)


# --- agrégation de plusieurs tours -------------------------------------------------------------------
def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _merge_metric(vals: list):
    """Fusionne la même mesure sur plusieurs tours : médiane des médianes, pire p95/max, n cumulé. Autres valeurs : médiane si numérique."""
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    if all(isinstance(v, dict) and "mediane" in v for v in vals):
        return {"n": sum(v["n"] for v in vals), "mediane": round(_median([v["mediane"] for v in vals]), 2),
                "p95": max(v["p95"] for v in vals), "max": max(v["max"] for v in vals)}
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return round(_median(vals), 3)
    if all(isinstance(v, dict) for v in vals):
        keys = {k for v in vals for k in v}
        return {k: _merge_metric([v.get(k) for v in vals]) for k in sorted(keys)}
    return vals[len(vals) // 2]


def aggregate_rounds(results: dict) -> dict:
    """Un seul `run` par (outil, variante) : médiane des tours. Les tours individuels restent dans `rounds_detail`."""
    runs = results.get("runs", [])
    if len({r.get("round", 1) for r in runs}) <= 1:
        return results
    out = {k: v for k, v in results.items() if k != "runs"}
    out["rounds_detail"], out["runs"] = runs, []
    for tk, variant in dict.fromkeys((r["toolkit"], r["variant"]) for r in runs):
        grp = [r for r in runs if r["toolkit"] == tk and r["variant"] == variant]
        metrics = {k: _merge_metric([r.get("metrics", {}).get(k) for r in grp]) for k in {k for r in grp for k in r.get("metrics", {})}}
        phases = list(dict.fromkeys(ph for r in grp for ph in (r.get("resources") or {})))
        res = {ph: {"cpu": _merge_metric([((r.get("resources") or {}).get(ph) or {}).get("cpu") for r in grp]),
                    "ram_mo_max": max([((r.get("resources") or {}).get(ph) or {}).get("ram_mo_max", 0) for r in grp]),
                    "gpu_pct": _merge_metric([((r.get("resources") or {}).get(ph) or {}).get("gpu_pct") for r in grp])}
               for ph in phases if any((r.get("resources") or {}).get(ph) for r in grp)}
        out["runs"].append({"toolkit": tk, "variant": variant, "metrics": metrics, "resources": res,
                            "errors": [f"tour {r.get('round', '?')}: {e}" for r in grp for e in r.get("errors", [])],
                            "rounds_ok": sum(1 for r in grp if r.get("metrics"))})
    return out


def _fmt_res(r: dict | None) -> str:
    if not r:
        return NM
    cpu = r.get("cpu")
    return (f"CPU {cpu['mediane']}% (p95 {cpu['p95']}%) · RAM max {r['ram_mo_max']} Mo" if cpu else NM) + \
        (f" · GPU {r['gpu_pct']['mediane']}%" if r.get("gpu_pct") else "")
