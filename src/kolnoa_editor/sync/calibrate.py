"""`kolnoa sync-calibrate` : mesure le moteur de synchro sur de VRAIS rushes, en lecture seule.

Garanties (testées) :
  * aucun fichier du dossier de rushes n'est ouvert en écriture, créé, renommé ni supprimé ; toutes les sorties vont dans
    un dossier de rapport qui ne peut pas se trouver dans les rushes ;
  * taille et date de chaque fichier lu sont relevées avant et après : le rapport dit si un original a changé ;
  * jamais de téléchargement ni de copie des médias ; seuls des chiffres sont écrits dans le rapport ;
  * sans option, rien n'est lu au-delà d'un inventaire ffprobe (`--dry-run` ne décode aucun média).

Le rapport donne, pour chaque paire : décalage, dérive, confiance, état, temps de calcul, écart avec l'indice des
métadonnées, écart avec des repères posés à la main (`--truth`) et cohérence en boucle entre angles. Les seuils proposés
ne sont jamais appliqués tout seuls.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

import numpy as np
from zoneinfo import ZoneInfo

from ..ffmpeg import ffmpeg_bin
from .audio import estimate_audio_sync
from .config import SyncConfig
from .sources import FileSource
from .visual import LumaSeries, luma_series, propose_visual_sync

LOCAL_TZ = "Europe/Paris"      # fuseau des noms de fichiers horodatés par les boîtiers (DJI) ; réglable (--tz)

AUDIO_EXT = {".wav", ".bwf", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff"}
VIDEO_EXT = {".mp4", ".mov", ".mxf", ".mts", ".m2ts", ".m4v", ".avi", ".mkv"}

# Dossiers (relatifs au dossier du mariage) des scénarios de calibration convenus avec le réalisateur / ChatGPT.
SCENARIOS: dict[str, dict] = {
    "mairie": {
        "title": "MAIRIE : CAM 1, CAM 2, MIC 1, MIC 2",
        "audio": ["RUSHES/MAIRIE/AUDIO/MIC 1", "RUSHES/MAIRIE/AUDIO/MIC 2"],
        "video": ["RUSHES/MAIRIE/CAM 1", "RUSHES/MAIRIE/CAM 2"],
    },
    "jeremy": {
        "title": "PREPA HOUPPA RECEPTION / JEREMY : caméra, audio, drone",
        "audio": ["RUSHES/PREPA HOUPPA RECEPTION/JEREMY/AUDIO"],
        "video": ["RUSHES/PREPA HOUPPA RECEPTION/JEREMY/CAM", "RUSHES/PREPA HOUPPA RECEPTION/JEREMY/DRONE"],
    },
    "clip": {
        "title": "CLIP : CAM 1, CAM 2",
        "audio": [],
        "video": ["RUSHES/CLIP/CAM 1", "RUSHES/CLIP/CAM 2"],
    },
}


# ------------------------------------------------------------------------------------------- chemins
def resolve_root(root: str, root_maps: list[str] | None = None) -> Path:
    """Chemin utilisable sur cette machine. Un chemin Windows (`G:\\Mon Drive\\...`) est accepté tel quel sous Windows ;
    ailleurs il faut une correspondance `--root-map "G:\\Mon Drive\\...=/chemin/local"` (le dossier réellement monté)."""
    for m in root_maps or []:
        src, _, dst = m.partition("=")
        if src and dst and _norm(root).lower().startswith(_norm(src).lower()):
            tail = _norm(root)[len(_norm(src)):].strip("/")
            return Path(dst) / tail if tail else Path(dst)
    if re.match(r"^[A-Za-z]:[\\/]", root):
        import os
        if os.name != "nt":
            raise ValueError(f"chemin Windows « {root} » inaccessible depuis cette machine : donner --root-map "
                             f"« {root}=/dossier/monté/localement »")
    return Path(root)


def _norm(p: str) -> str:
    return str(PureWindowsPath(p).as_posix()) if re.match(r"^[A-Za-z]:[\\/]", p) else str(PurePosixPath(p))


def ensure_outside(out: Path, root: Path) -> None:
    o, r = out.resolve(), root.resolve()
    if o == r or r in o.parents:
        raise ValueError(f"le dossier de rapport ({o}) ne doit pas se trouver dans les rushes ({r}) : "
                         "les originaux ne sont jamais modifiés")


# ------------------------------------------------------------------------------------------- inventaire
@dataclass
class MediaRecord:
    rel: str
    size: int
    mtime_ns: int
    kind: str                        # 'video' | 'audio'
    duration: float | None = None
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    audio_rate: int | None = None
    has_audio: bool = False
    audio_empty: bool | None = None            # silence numérique (None = non vérifié)
    creation_time: str | None = None           # balise du fichier (UTC)
    timecode: str | None = None
    name_time: str | None = None               # horodatage lu dans le nom du fichier (heure locale du boîtier)
    start_utc: str | None = None               # meilleure estimation du début (balise sinon nom)
    start_source: str = "inconnu"
    error: str | None = None


_NAME_PATTERNS = [
    (re.compile(r"DJI_(\d{14})_"), "%Y%m%d%H%M%S"),
    (re.compile(r"DJI_\d+_(\d{8}_\d{6})"), "%Y%m%d_%H%M%S"),
    (re.compile(r"(\d{8})_(\d{6})(?!\d)"), "%Y%m%d%H%M%S"),
]


def name_time(name: str) -> datetime | None:
    for rx, fmt in _NAME_PATTERNS:
        m = rx.search(name)
        if m:
            txt = "".join(m.groups()) if rx.groups > 1 else m.group(1)
            if fmt == "%Y%m%d_%H%M%S":
                txt = m.group(1)
            try:
                return datetime.strptime(txt, fmt)
            except ValueError:
                continue
    return None


def _fraction(s: str | None) -> float | None:
    if not s or "/" not in s:
        return None
    a, b = s.split("/")
    try:
        return float(a) / float(b) if float(b) else None
    except ValueError:
        return None


def ffprobe_json(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200] or "ffprobe a échoué")
    return json.loads(out.stdout)


def audio_is_empty(path: Path, duration: float | None, peak: float = 1e-5, probes: int = 6) -> bool:
    """Vrai si l'audio est à zéro numérique : 6 prises de 2 s réparties dans le fichier, lecture seule."""
    dur = duration or 0.0
    peaks = []
    for t in np.linspace(0.0, max(dur - 2.0, 0.0), probes):
        r = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", f"{t:.3f}", "-t", "2", "-i", str(path), "-vn", "-ac", "1",
                            "-ar", "8000", "-f", "f32le", "-"], capture_output=True, timeout=120)
        x = np.frombuffer(r.stdout, dtype="<f4")
        peaks.append(float(np.max(np.abs(x))) if len(x) else 0.0)
    return max(peaks) < peak


def media_record(root: Path, path: Path, check_audio: bool = False, tz: str = LOCAL_TZ) -> MediaRecord:
    st = path.stat()
    rel = path.relative_to(root).as_posix()
    kind = "audio" if path.suffix.lower() in AUDIO_EXT else "video"
    rec = MediaRecord(rel, st.st_size, st.st_mtime_ns, kind)
    nt = name_time(path.name)
    rec.name_time = nt.isoformat() if nt else None
    try:
        j = ffprobe_json(path)
    except Exception as exc:                       # fichier illisible : on le dit, on continue
        rec.error = str(exc)
        return rec
    fmt = j.get("format", {})
    rec.duration = float(fmt["duration"]) if fmt.get("duration") else None
    tags = fmt.get("tags", {}) or {}
    rec.creation_time = tags.get("creation_time")
    for s in j.get("streams", []):
        tag = s.get("tags", {}) or {}
        if "timecode" in tag and not rec.timecode:
            rec.timecode = tag["timecode"]
        if s.get("codec_type") == "video" and rec.video_codec is None and s.get("disposition", {}).get("attached_pic") != 1:
            rec.video_codec, rec.width, rec.height = s.get("codec_name"), s.get("width"), s.get("height")
            rec.fps = _fraction(s.get("avg_frame_rate")) or _fraction(s.get("r_frame_rate"))
        elif s.get("codec_type") == "audio" and not rec.has_audio:
            rec.has_audio, rec.audio_codec = True, s.get("codec_name")
            rec.audio_channels, rec.audio_rate = s.get("channels"), int(s["sample_rate"]) if s.get("sample_rate") else None
    if rec.creation_time:
        try:
            rec.start_utc = datetime.fromisoformat(rec.creation_time.replace("Z", "+00:00")).isoformat()
            rec.start_source = "balise creation_time"
        except ValueError:
            pass
    if rec.start_utc is None and nt is not None:
        rec.start_utc = nt.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc).isoformat()
        rec.start_source = f"nom du fichier (heure locale {tz})"
    if check_audio and rec.has_audio:
        try:
            rec.audio_empty = audio_is_empty(path, rec.duration)
        except Exception:
            rec.audio_empty = None
    return rec


def walk_media(folder: Path) -> list[Path]:
    exts = AUDIO_EXT | VIDEO_EXT
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in exts)


def inventory(root: Path, folders: list[str], check_audio: bool = False, limit: int | None = None,
              log=print, tz: str = LOCAL_TZ) -> list[MediaRecord]:
    recs: list[MediaRecord] = []
    for f in folders:
        d = root / f
        if not d.is_dir():
            log(f"  ! dossier absent : {f}")
            continue
        files = walk_media(d)
        if limit:
            files = files[:limit]
        log(f"  {f} : {len(files)} fichier(s)")
        for p in files:
            recs.append(media_record(root, p, check_audio, tz))
    return recs


# ------------------------------------------------------------------------------------------- candidats
def interval(r: MediaRecord) -> tuple[float, float] | None:
    if not r.start_utc or not r.duration:
        return None
    s = datetime.fromisoformat(r.start_utc).timestamp()
    return s, s + r.duration


@dataclass
class Pair:
    kind: str            # 'audio' | 'visuel'
    ref: str
    sig: str
    expected_offset_s: float | None      # décalage attendu d'après les métadonnées : début_sig - début_ref
    gap_s: float                         # distance entre intervalles (0 = se recouvrent d'après les métadonnées)
    note: str = ""


def candidate_pairs(refs: list[MediaRecord], sigs: list[MediaRecord], kind: str, tol_s: float,
                    max_pairs: int) -> list[Pair]:
    out: list[Pair] = []
    for r in refs:
        ir = interval(r)
        for s in sigs:
            if s.rel == r.rel:
                continue
            isg = interval(s)
            if ir is None or isg is None:
                out.append(Pair(kind, r.rel, s.rel, None, float("inf"), "horodatage manquant"))
                continue
            gap = max(ir[0] - isg[1], isg[0] - ir[1], 0.0)
            if any(o.ref == s.rel and o.sig == r.rel for o in out):       # même paire dans l'autre sens
                continue
            if gap <= tol_s:
                out.append(Pair(kind, r.rel, s.rel, isg[0] - ir[0], gap))
    out.sort(key=lambda p: (p.gap_s, p.sig))
    return out[:max_pairs]


# ------------------------------------------------------------------------------------------- exécution
@dataclass
class AudioResult:
    ref: str
    sig: str
    status: str
    confidence: float
    offset_s: float
    drift_ppm: float
    drift_measured: bool
    elapsed_s: float
    windows: int
    valid: int
    inliers: int
    median_psr: float | None
    env_psr: float | None
    rival_peak_ratio: float | None
    notes: list[str]
    window_table: list[dict]
    hint_expected_s: float | None = None
    hint_gap_s: float | None = None          # estimé - attendu d'après les métadonnées
    with_hint: dict | None = None
    truth_error_ms: float | None = None


@dataclass
class VisualRow:
    ref: str
    sig: str
    status: str
    events_ref: int
    events_sig: int
    best_offset_s: float | None
    best_confidence: float | None
    matched_events: int | None
    coincidence_risk: float | None
    candidates: list[dict]
    elapsed_s: float
    notes: list[str]
    hint_expected_s: float | None = None
    hint_gap_s: float | None = None
    truth_error_ms: float | None = None
    luma_stats: dict = field(default_factory=dict)      # variation image à image de la luminosité moyenne (réf, sig)


def _hint(p: Pair, tol: float) -> tuple[float, float] | None:
    return None if p.expected_offset_s is None else (p.expected_offset_s, tol)


def run_audio(root: Path, pairs: list[Pair], cfg: SyncConfig, with_hint: bool, hint_tol_s: float, log=print) -> list[AudioResult]:
    res: list[AudioResult] = []
    cache: dict[str, FileSource] = {}

    def src(rel: str) -> FileSource:
        if rel not in cache:
            cache[rel] = FileSource(root / rel, rate=cfg.rate)
        return cache[rel]

    for i, p in enumerate(pairs, 1):
        log(f"  audio {i}/{len(pairs)} : {p.sig}  →  {p.ref}")
        t0 = time.perf_counter()
        try:
            est = estimate_audio_sync(src(p.ref), src(p.sig), cfg)
        except Exception as exc:
            res.append(AudioResult(p.ref, p.sig, "erreur", 0.0, 0.0, 0.0, False, time.perf_counter() - t0, 0, 0, 0, None,
                                   None, None, [f"{type(exc).__name__}: {exc}"], []))
            continue
        m = est.metrics
        row = AudioResult(
            p.ref, p.sig, est.status, est.confidence, est.offset, est.drift_ppm, est.drift_measured, time.perf_counter() - t0,
            int(m.get("windows", 0)), int(m.get("valid", 0)), int(m.get("inliers", 0)), m.get("median_psr"), m.get("env_psr"),
            m.get("rival_peak_ratio"), list(est.notes),
            [{"t": round(w.t_angle, 1), "offset": None if w.offset is None else round(w.offset, 4), "psr": round(w.psr, 1),
              "valide": w.valid, "silence": w.silent, "inlier": w.inlier} for w in est.windows],
            hint_expected_s=p.expected_offset_s)
        if p.expected_offset_s is not None and est.status in ("ok", "incertain", "discontinu"):
            row.hint_gap_s = est.offset - p.expected_offset_s
        if with_hint and (h := _hint(p, hint_tol_s)) is not None:
            e2 = estimate_audio_sync(src(p.ref), src(p.sig), cfg, hint=h)
            row.with_hint = {"status": e2.status, "confidence": e2.confidence, "offset_s": e2.offset,
                             "drift_ppm": e2.drift_ppm}
        res.append(row)
    return res


def luma_delta_stats(s: LumaSeries) -> dict:
    d = np.abs(np.diff(s.luma)) if len(s.luma) > 1 else np.zeros(1)
    return {"duree_s": round(s.duration, 1), "luma_min": round(float(s.luma.min()), 1), "luma_max": round(float(s.luma.max()), 1),
            "delta_median": round(float(np.median(d)), 3), "delta_p99": round(float(np.percentile(d, 99)), 2),
            "delta_max": round(float(d.max()), 2)}


class VisualCache:
    def __init__(self, root: Path, cache_dir: Path, cfg: SyncConfig, max_seconds: float):
        self.root, self.dir, self.cfg, self.max_seconds = root, cache_dir, cfg, max_seconds
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, rel: str) -> LumaSeries:
        p = self.root / rel
        st = p.stat()
        key = hashlib.sha1(f"{rel}|{st.st_size}|{st.st_mtime_ns}|{self.max_seconds}|{self.cfg.visual_fps}".encode()).hexdigest()[:16]
        f = self.dir / f"{key}.npy"
        if f.exists():
            return LumaSeries(np.load(f), self.cfg.visual_fps)
        s = luma_series(p, self.cfg, tmp_dir=self.dir, max_seconds=self.max_seconds)
        np.save(f, s.luma)
        return s


def run_visual(root: Path, pairs: list[Pair], cfg: SyncConfig, cache: VisualCache, hint_tol_s: float,
               use_hint: bool, log=print) -> list[VisualRow]:
    rows: list[VisualRow] = []
    for i, p in enumerate(pairs, 1):
        log(f"  visuel {i}/{len(pairs)} : {p.sig}  →  {p.ref}")
        t0 = time.perf_counter()
        try:
            ref, sig = cache.get(p.ref), cache.get(p.sig)
            stats = {k: luma_delta_stats(v) for k, v in (("ref", ref), ("sig", sig))}
            h = _hint(p, hint_tol_s) if use_hint else None
            r = propose_visual_sync(ref, sig, cfg, hint=h)
        except Exception as exc:
            rows.append(VisualRow(p.ref, p.sig, "erreur", 0, 0, None, None, None, None, [], time.perf_counter() - t0,
                                  [f"{type(exc).__name__}: {exc}"]))
            continue
        best = r.candidates[0] if r.candidates else None
        row = VisualRow(p.ref, p.sig, r.status, r.events_ref, r.events_sig,
                        None if best is None else best.offset, None if best is None else best.confidence,
                        None if best is None else best.matched_events,
                        None if best is None else best.coincidence_risk,
                        [{"offset_s": round(c.offset, 3), "confidence": c.confidence, "evenements": c.matched_events,
                          "risque_coincidence": round(c.coincidence_risk, 4), "psr": round(c.psr, 1)} for c in r.candidates],
                        time.perf_counter() - t0, list(r.notes), hint_expected_s=p.expected_offset_s, luma_stats=stats)
        if best is not None and p.expected_offset_s is not None:
            row.hint_gap_s = best.offset - p.expected_offset_s
        rows.append(row)
    return rows


# ------------------------------------------------------------------------------------------- repères manuels
def apply_truth(truth: list[dict], audio: list[AudioResult], visual: list[VisualRow]) -> list[dict]:
    """Repères vérifiés à l'image par le réalisateur : {ref, sig, t_ref, t_sig} = même instant réel dans les deux fichiers.
    Décalage attendu = t_ref − t_sig. Renvoie une ligne d'erreur par repère retrouvé."""
    out = []
    for t in truth:
        want = float(t["t_ref"]) - float(t["t_sig"])
        for r in audio:
            if r.ref == t["ref"] and r.sig == t["sig"]:
                r.truth_error_ms = (r.offset_s - want) * 1000.0
                out.append({"type": "audio", "ref": r.ref, "sig": r.sig, "etat": r.status, "confiance": r.confidence,
                            "erreur_ms": r.truth_error_ms})
        for v in visual:
            if v.ref == t["ref"] and v.sig == t["sig"] and v.best_offset_s is not None:
                v.truth_error_ms = (v.best_offset_s - want) * 1000.0
                out.append({"type": "visuel", "ref": v.ref, "sig": v.sig, "etat": v.status, "confiance": v.best_confidence,
                            "erreur_ms": v.truth_error_ms})
    return out


# ------------------------------------------------------------------------------------------- constats de calibration
def threshold_findings(audio: list[AudioResult]) -> dict:
    """Ce que les fenêtres réelles disent des seuils : PSR des fenêtres qui obéissent au décalage dominant (inliers) et
    PSR des pics parasites. Chiffres bruts, à confirmer ; rien n'est appliqué."""
    good, stray = [], []
    for r in audio:
        for w in r.window_table:
            if w["silence"] or w["offset"] is None:
                continue
            (good if w["inlier"] else stray).append(w["psr"])
    out: dict = {"fenetres_coherentes": len(good), "fenetres_aberrantes": len(stray)}
    if good:
        out["psr_coherentes"] = {"min": min(good), "median": float(np.median(good)), "p90": float(np.percentile(good, 90))}
    if stray:
        out["psr_aberrantes"] = {"median": float(np.median(stray)), "max": max(stray)}
    if len(good) >= 10 and len(stray) >= 5:
        best = None
        for thr in np.arange(6.0, 30.0, 1.0):
            kept = sum(g >= thr for g in good) / len(good)
            rej = sum(s < thr for s in stray) / len(stray)
            score = kept + rej
            if best is None or score > best[0]:
                best = (score, float(thr), kept, rej)
        out["min_psr_suggere"] = {"valeur": best[1], "fenetres_coherentes_gardees": round(best[2], 2),
                                  "aberrantes_rejetees": round(best[3], 2),
                                  "avertissement": "estimé sur peu de données ; à confirmer avec plus de rushes"}
    else:
        out["min_psr_suggere"] = "pas assez de fenêtres pour proposer un seuil (≥ 10 cohérentes et ≥ 5 aberrantes requises)"
    return out


def loop_closure(audio: list[AudioResult]) -> list[dict]:
    """Cohérence en boucle : A→M, B→M et A→B doivent vérifier off(A→B) = off(A→M) − off(B→M). Sans repère manuel, c'est la
    meilleure mesure d'erreur disponible. Seules les paires utilisables entrent."""
    by = {(r.ref, r.sig): r for r in audio if r.status in ("ok", "incertain")}
    out = []
    for (m, a), ra in by.items():
        for (m2, b), rb in by.items():
            if m2 == m and a < b and (a, b) in by:
                direct = by[(a, b)]
                out.append({"ref": m, "a": a, "b": b, "ecart_ms": ((ra.offset_s - rb.offset_s) - direct.offset_s) * 1000.0})
    return out


# ------------------------------------------------------------------------------------------- intégrité
def snapshot(root: Path, rels: list[str]) -> dict[str, tuple[int, int]]:
    out = {}
    for r in rels:
        st = (root / r).stat()
        out[r] = (st.st_size, st.st_mtime_ns)
    return out


def changed_files(before: dict, after: dict) -> list[str]:
    return sorted(k for k in before if after.get(k) != before[k])


# ------------------------------------------------------------------------------------------- rapport
def _fmt_off(x: float, fps: float = 25.0) -> str:
    return f"{x:+.3f} s ({x * fps:+.1f} i à {fps:g} i/s)"


def write_report(out: Path, meta: dict, inv: list[MediaRecord], audio: list[AudioResult], visual: list[VisualRow],
                 pairs_a: list[Pair], pairs_v: list[Pair], truth_rows: list[dict], findings: dict,
                 closure: list[dict], unchanged: bool | None, changed: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = {"meta": meta, "inventaire": [asdict(r) for r in inv], "paires_audio": [asdict(p) for p in pairs_a],
            "paires_visuel": [asdict(p) for p in pairs_v], "audio": [asdict(a) for a in audio],
            "visuel": [asdict(v) for v in visual], "reperes_manuels": truth_rows, "constats_seuils": findings,
            "fermeture_en_boucle": closure, "originaux_inchanges": unchanged, "fichiers_modifies": changed}
    (out / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "inventory.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        cols = ["rel", "kind", "size", "duration", "fps", "width", "height", "video_codec", "audio_codec", "audio_channels",
                "audio_rate", "has_audio", "audio_empty", "creation_time", "timecode", "name_time", "start_source", "error"]
        w.writerow(cols)
        for r in inv:
            d = asdict(r)
            w.writerow([d[c] for c in cols])
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["type", "ref", "sig", "etat", "confiance", "decalage_s", "derive_ppm", "ecart_metadonnees_s", "erreur_reperes_ms",
                    "secondes_calcul"])
        for a in audio:
            w.writerow(["audio", a.ref, a.sig, a.status, round(a.confidence, 3), round(a.offset_s, 4), round(a.drift_ppm, 1),
                        "" if a.hint_gap_s is None else round(a.hint_gap_s, 2),
                        "" if a.truth_error_ms is None else round(a.truth_error_ms, 1), round(a.elapsed_s, 1)])
        for v in visual:
            w.writerow(["visuel", v.ref, v.sig, v.status, "" if v.best_confidence is None else round(v.best_confidence, 3),
                        "" if v.best_offset_s is None else round(v.best_offset_s, 3), "",
                        "" if v.hint_gap_s is None else round(v.hint_gap_s, 2),
                        "" if v.truth_error_ms is None else round(v.truth_error_ms, 1), round(v.elapsed_s, 1)])
    (out / "report.md").write_text(render_markdown(meta, inv, audio, visual, truth_rows, findings, closure, unchanged, changed),
                                   encoding="utf-8")


def render_markdown(meta, inv, audio, visual, truth_rows, findings, closure, unchanged, changed) -> str:
    L: list[str] = []
    L += [f"# Calibration de la synchro — {meta['scenario_title']}", "",
          f"- Dossier : `{meta['root']}` (lecture seule)",
          f"- Généré le {meta['generated']} ; moteur : seuils par défaut `SyncConfig` (valeurs initiales, non calibrées)",
          f"- **Originaux inchangés : {'oui' if unchanged else ('NON : ' + ', '.join(changed) if unchanged is False else 'non vérifié')}** "
          "(taille et date relevées avant/après)", ""]
    L += ["## Inventaire", "", "| dossier | fichiers | durée totale | Go | audio présent | audio vide | fps | résolutions |", "|---|---|---|---|---|---|---|---|"]
    by: dict[str, list[MediaRecord]] = {}
    for r in inv:
        by.setdefault(str(PurePosixPath(r.rel).parent), []).append(r)
    for d, rs in by.items():
        dur = sum(r.duration or 0 for r in rs)
        fps = sorted({round(r.fps, 2) for r in rs if r.fps})
        res = sorted({f"{r.width}x{r.height}" for r in rs if r.width})
        empties = sum(1 for r in rs if r.audio_empty)
        checked = sum(1 for r in rs if r.audio_empty is not None)
        L.append(f"| {d} | {len(rs)} | {dur / 60:.1f} min | {sum(r.size for r in rs) / 1e9:.2f} | "
                 f"{sum(1 for r in rs if r.has_audio)} | {empties}/{checked} vérifiés | {fps} | {res} |")
    L += ["", "## Synchro audio", ""]
    if not audio:
        L += ["Aucune paire audio exploitable (voir « Limites »).", ""]
    else:
        L += ["| fichier calé | sur | état | confiance* | décalage | dérive | fenêtres valides/cohérentes | écart métadonnées | calcul |",
              "|---|---|---|---|---|---|---|---|---|"]
        for a in audio:
            drift = f"{a.drift_ppm:+.1f} ppm" + ("" if a.drift_measured else " (non mesurée)")
            gap = "—" if a.hint_gap_s is None else f"{a.hint_gap_s:+.1f} s"
            L.append(f"| {PurePosixPath(a.sig).name} | {PurePosixPath(a.ref).name} | **{a.status}** | {a.confidence:.2f} | "
                     f"{_fmt_off(a.offset_s) if a.status not in ('impossible', 'erreur') else '—'} | {drift if a.status not in ('impossible', 'erreur') else '—'} | "
                     f"{a.valid}/{a.windows} · {a.inliers} | {gap} | {a.elapsed_s:.1f} s |")
        L += ["", "\\* score heuristique de classement, **pas** une probabilité de justesse.", ""]
        for a in audio:
            for n in a.notes:
                L.append(f"- `{PurePosixPath(a.sig).name}` : {n}")
            if a.with_hint:
                L.append(f"- `{PurePosixPath(a.sig).name}` avec indice métadonnées : {a.with_hint['status']} "
                         f"(confiance {a.with_hint['confidence']:.2f}, décalage {a.with_hint['offset_s']:+.3f} s)")
        L.append("")
        for a in audio:
            if a.window_table and a.status in ("incertain", "discontinu", "ok"):
                L += [f"<details><summary>Fenêtres de `{PurePosixPath(a.sig).name}`</summary>", "",
                      "| t (s) | décalage (s) | PSR | valide | cohérente |", "|---|---|---|---|---|"]
                for w in a.window_table:
                    L.append(f"| {w['t']} | {w['offset']} | {w['psr']} | {w['valide']} | {w['inlier']} |")
                L += ["", "</details>", ""]
    L += ["## Synchro visuelle assistée (expérimentale, jamais appliquée seule)", ""]
    if not visual:
        L += ["Aucune paire visuelle analysée.", ""]
    else:
        L += ["| fichier calé | sur | état | événements (réf/sig) | décalage proposé | confiance (≤ 0,6) | événements concordants | risque de coïncidence (heuristique) | écart métadonnées |",
              "|---|---|---|---|---|---|---|---|---|"]
        for v in visual:
            off = "—" if v.best_offset_s is None else _fmt_off(v.best_offset_s)
            conf = "—" if v.best_confidence is None else f"{v.best_confidence:.2f}"
            risk = "—" if v.coincidence_risk is None else f"{v.coincidence_risk:.3f}"
            gap = "—" if v.hint_gap_s is None else f"{v.hint_gap_s:+.1f} s"
            L.append(f"| {PurePosixPath(v.sig).name} | {PurePosixPath(v.ref).name} | **{v.status}** | {v.events_ref}/{v.events_sig} | "
                     f"{off} | {conf} | {v.matched_events if v.matched_events is not None else '—'} | {risk} | {gap} |")
        L.append("")
        for n in dict.fromkeys(f"`{PurePosixPath(v.sig).name}` : {n}" for v in visual for n in v.notes):
            L.append(f"- {n}")
        L.append("")
        L += ["Variation image à image de la luminosité moyenne (seuil d'événement actuel : "
              f"{meta['config']['visual_event_min_delta']} niveaux) :", "",
              "| fichier | durée | luminosité min–max | |Δ| médian | |Δ| p99 | |Δ| max |", "|---|---|---|---|---|---|"]
        seen = set()
        for v in visual:
            for k, name in (("ref", v.ref), ("sig", v.sig)):
                st = v.luma_stats.get(k)
                if st and name not in seen:
                    seen.add(name)
                    L.append(f"| {PurePosixPath(name).name} | {st['duree_s']} s | {st['luma_min']}–{st['luma_max']} | "
                             f"{st['delta_median']} | {st['delta_p99']} | {st['delta_max']} |")
        L.append("")
    if closure:
        L += ["## Cohérence en boucle (sans repère manuel)", "", "| référence | A | B | écart de fermeture |", "|---|---|---|---|"]
        for c in closure:
            L.append(f"| {PurePosixPath(c['ref']).name} | {PurePosixPath(c['a']).name} | {PurePosixPath(c['b']).name} | {c['ecart_ms']:+.1f} ms |")
        L.append("")
    if truth_rows:
        L += ["## Erreurs mesurées sur repères posés à la main", "", "| type | fichier | état | confiance | erreur |", "|---|---|---|---|---|"]
        for t in truth_rows:
            L.append(f"| {t['type']} | {PurePosixPath(t['sig']).name} | {t['etat']} | {t['confiance']} | {t['erreur_ms']:+.1f} ms |")
        errs = [abs(t["erreur_ms"]) for t in truth_rows]
        L += ["", f"Erreur absolue médiane {np.median(errs):.1f} ms, max {max(errs):.1f} ms sur {len(errs)} repère(s).", ""]
    else:
        L += ["## Erreurs sur repères manuels", "",
              "Aucun repère fourni (`--truth`). Sans repère vérifié à l'image, **aucune erreur absolue ne peut être affirmée** : "
              "ni la justesse des décalages ni les taux de faux positifs ne sont démontrés par ce rapport.", ""]
    L += ["## Ce que les fenêtres réelles disent des seuils", "", "```json", json.dumps(findings, ensure_ascii=False, indent=2), "```", ""]
    L += ["## Limites", ""] + [f"- {x}" for x in meta.get("limites", [])] + [""]
    return "\n".join(L)


# ------------------------------------------------------------------------------------------- orchestration
@dataclass
class Options:
    root: Path
    out: Path
    scenario: str = "mairie"
    audio_dirs: list[str] = field(default_factory=list)
    video_dirs: list[str] = field(default_factory=list)
    dry_run: bool = False
    inventory_only: bool = False
    check_audio: bool = True
    with_hint: bool = False
    visual: bool = True
    visual_use_hint: bool = False
    max_pairs: int = 12
    clock_tol_s: float = 7200.0
    max_video_seconds: float = 300.0
    inventory_limit: int | None = None
    tz: str = LOCAL_TZ
    only: list[str] = field(default_factory=list)      # sous-chaînes : ne garder que les fichiers dont le chemin les contient
    truth: Path | None = None
    cfg: SyncConfig = field(default_factory=SyncConfig)


def plan_text(opts: Options, inv: list[MediaRecord], pa: list[Pair], pv: list[Pair]) -> str:
    size = {r.rel: r for r in inv}
    lines = [f"Plan de calibration — {SCENARIOS.get(opts.scenario, {}).get('title', opts.scenario)}", "",
             f"{len(inv)} fichier(s) inventoriés ; {len(pa)} paire(s) audio, {len(pv)} paire(s) visuelle(s) (écart d'horloge toléré "
             f"{opts.clock_tol_s:.0f} s)", ""]
    for kind, ps in (("AUDIO", pa), ("VISUEL", pv)):
        for p in ps:
            s, r = size.get(p.sig), size.get(p.ref)
            gb = ((s.size if s else 0) + (r.size if r else 0)) / 1e9
            lines.append(f"  [{kind}] {p.sig} → {p.ref}  (écart métadonnées {p.gap_s:.0f} s, {gb:.2f} Go à lire)")
    lines += ["", ("Aucun média n'a été décodé (dry-run) ; " if opts.dry_run or opts.inventory_only else "")
              + "les originaux ne sont jamais modifiés."]
    return "\n".join(lines)


LIMITES = [
    "Les seuils par défaut sont des valeurs initiales ; ce rapport ne les change pas. `suggested_sync_config.json` n'est "
    "qu'une proposition à valider.",
    "Sans repères posés à la main et vérifiés à l'image, la justesse absolue n'est pas démontrée (seulement la cohérence "
    "interne : fermeture en boucle, accord avec les métadonnées).",
    "Les horloges des boîtiers ne sont pas réglées ensemble : les écarts de métadonnées servent d'indice de sélection de paires, "
    "jamais de synchro.",
    "Les pistes audio à zéro numérique (silence) ne peuvent pas être synchronisées par le son : synchro visuelle assistée ou manuelle.",
    "La synchro visuelle n'analyse que les `max_video_seconds` premières secondes de chaque vidéo.",
]


def run(opts: Options, log=print) -> dict:
    root, out = opts.root, opts.out
    if not root.is_dir():
        raise FileNotFoundError(f"dossier introuvable : {root}")
    ensure_outside(out, root)
    sc = SCENARIOS.get(opts.scenario)
    audio_dirs = opts.audio_dirs or (sc["audio"] if sc else [])
    video_dirs = opts.video_dirs or (sc["video"] if sc else [])
    if not audio_dirs and not video_dirs:
        raise ValueError("aucun dossier à analyser : choisir --scenario ou donner --audio-dir / --video-dir")
    log("Inventaire (ffprobe, lecture seule)…")
    inv = inventory(root, audio_dirs + video_dirs, check_audio=opts.check_audio and not opts.dry_run, limit=opts.inventory_limit, log=log, tz=opts.tz)
    if opts.only:
        inv = [r for r in inv if any(s in r.rel for s in opts.only)]
    audios = [r for r in inv if r.kind == "audio" or any(r.rel.startswith(d + "/") for d in audio_dirs)]
    videos = [r for r in inv if r not in audios]
    before = snapshot(root, [r.rel for r in inv])
    # paires audio : un enregistreur de référence (premier dossier audio) ; autres enregistreurs et caméras avec son = angles
    pa: list[Pair] = []
    if audios:
        first_dir = audio_dirs[0] if audio_dirs else None
        refs = [r for r in audios if first_dir and r.rel.startswith(first_dir + "/")] or audios[:1]
        sig_audio = [r for r in audios if r not in refs]
        sig_cams = [r for r in videos if r.has_audio and r.audio_empty is not True]
        pa = candidate_pairs(refs, sig_audio + sig_cams, "audio", opts.clock_tol_s, opts.max_pairs)
    # paires visuelles : pour chaque caméra muette ou sans audio exploitable, contre l'autre caméra
    pv: list[Pair] = []
    if opts.visual and videos:
        silent = [r for r in videos if (not r.has_audio) or r.audio_empty is True]
        first_vdir = video_dirs[0] if video_dirs else None
        vrefs = [r for r in videos if first_vdir and r.rel.startswith(first_vdir + "/")] or videos
        pv = candidate_pairs(vrefs, [r for r in silent if r not in vrefs] or silent, "visuel", opts.clock_tol_s, opts.max_pairs)
    meta = {"scenario": opts.scenario, "scenario_title": sc["title"] if sc else opts.scenario, "root": str(root),
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "limites": LIMITES,
            "options": {"max_pairs": opts.max_pairs, "clock_tol_s": opts.clock_tol_s, "max_video_seconds": opts.max_video_seconds,
                        "with_hint": opts.with_hint}, "config": opts.cfg.to_dict()}
    out.mkdir(parents=True, exist_ok=True)
    plan = plan_text(opts, inv, pa, pv)
    (out / "plan.txt").write_text(plan, encoding="utf-8")
    log(plan)
    audio_res: list[AudioResult] = []
    visual_res: list[VisualRow] = []
    if not (opts.dry_run or opts.inventory_only):
        if pa:
            log("Synchro audio…")
            audio_res = run_audio(root, pa, opts.cfg, opts.with_hint, opts.clock_tol_s, log)
        if pv:
            log("Synchro visuelle…")
            visual_res = run_visual(root, pv, opts.cfg, VisualCache(root, out / "cache", opts.cfg, opts.max_video_seconds),
                                    opts.clock_tol_s, opts.visual_use_hint, log)
    truth_rows: list[dict] = []
    if opts.truth and opts.truth.exists():
        truth_rows = apply_truth(json.loads(opts.truth.read_text(encoding="utf-8")), audio_res, visual_res)
    findings = threshold_findings(audio_res)
    closure = loop_closure(audio_res)
    after = snapshot(root, [r.rel for r in inv])
    changed = changed_files(before, after)
    write_report(out, meta, inv, audio_res, visual_res, pa, pv, truth_rows, findings, closure, not changed, changed)
    sug = findings.get("min_psr_suggere")
    if isinstance(sug, dict):
        (out / "suggested_sync_config.json").write_text(json.dumps({"min_psr": sug["valeur"]}, indent=2), encoding="utf-8")
    return {"inventory": len(inv), "pairs_audio": len(pa), "pairs_visual": len(pv), "audio": audio_res, "visual": visual_res,
            "unchanged": not changed, "changed": changed, "out": out}
