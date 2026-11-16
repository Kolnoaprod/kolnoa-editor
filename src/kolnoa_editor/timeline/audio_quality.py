"""Mesure de la qualité sonore d'une source et PROPOSITION (jamais application) de la meilleure source par séquence.

Trois choses distinctes, qu'on ne mélange pas :
  - la synchronisation : une source non synchronisée de façon fiable est ÉCARTÉE, pas classée ;
  - la qualité sonore mesurée : niveau, saturation, plancher de bruit, contraste, silence, phase ;
  - le choix éditorial : qui on veut entendre à cet endroit. Il reste humain (`assign_master`).

Chaque critère a une valeur mesurée, une note, un poids et une phrase d'explication : on peut contester le classement.
Ce que la mesure ne dit PAS (intelligibilité de la parole, naturel de la prise de son, intérêt du propos) est écrit en clair
dans `caveats`. Les seuils sont des points de départ à recaler sur de vrais rushes, pas des vérités.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .audio_mix import measure_loudness
from .model import Timeline, TimelineError

WINDOW_S = 0.05                 # fenêtre de niveau : 50 ms
SILENCE_DB = -80.0                # « silence » = micro coupé ou absent, pas une pause de parole (le bruit de salle est au-dessus)
MAX_ANALYSIS_S = 600.0          # on n'analyse jamais plus de 10 minutes d'un coup
DECODE_SR = 48000

# Seuils et poids : documentés dans docs/AUDIO.md, à recaler sur de vrais rushes.
NOISE_BEST_DB, NOISE_WORST_DB = -70.0, -40.0
CONTRAST_BEST_DB, CONTRAST_WORST_DB = 30.0, 10.0
LEVEL_OK = (-35.0, -14.0)
LEVEL_ZERO = (-55.0, -5.0)
CLIP_ZERO_RATIO = 1e-3
SILENCE_ZERO_RATIO = 0.6
TIE_MARGIN = 0.08
WEIGHTS = {"saturation": 3.0, "bruit_de_fond": 2.0, "contraste": 2.0, "silence": 2.0, "niveau": 1.0, "phase": 1.0}

CAVEATS = ("La mesure ne juge ni l'intelligibilité de la parole, ni le naturel de la prise de son, ni l'intérêt éditorial "
           "(qui parle, quel plan) : c'est un conseil, la décision reste humaine.",
           "Les seuils sont des valeurs de départ, à recaler sur de vrais rushes avant de s'y fier.")


@dataclass
class AudioMeasure:
    path: str
    start_s: float
    duration_s: float
    sample_rate: int
    channels: int
    integrated_lufs: float | None
    true_peak_dbtp: float | None
    peak_dbfs: float | None
    rms_dbfs: float | None
    clip_ratio: float
    noise_floor_dbfs: float | None
    contrast_db: float | None            # écart entre les passages forts et le fond (≠ rapport signal/bruit mesuré)
    silence_ratio: float
    channel_correlation: float | None    # −1 (phases opposées) … +1 (identiques) ; None si mono


def _db(x: float) -> float:
    return -200.0 if x <= 1e-12 else 10.0 * np.log10(x)


def measure_audio(path: Path, start: float = 0.0, duration: float | None = None) -> AudioMeasure:
    """Analyse un fichier (ou une fenêtre [start, start+duration] de ce fichier). Ne modifie rien."""
    path = Path(path)
    if not path.is_file():
        raise TimelineError(f"média introuvable : {path}")
    if duration is None or duration > MAX_ANALYSIS_S:
        duration = MAX_ANALYSIS_S
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels,sample_rate",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    if probe.returncode != 0 or not probe.stdout.strip():
        raise TimelineError(f"{path.name} n'a pas de piste audio")
    sr_s, ch_s = probe.stdout.strip().split(",")[:2]
    sr, ch = int(sr_s), int(ch_s)
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", str(path), "-vn",
           "-ac", str(min(ch, 2)), "-ar", str(DECODE_SR), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).reshape(-1, min(ch, 2)).astype(np.float64)
    if len(x) == 0:
        raise TimelineError(f"{path.name}: rien à analyser entre {start:.1f} s et {start + duration:.1f} s")
    n = int(WINDOW_S * DECODE_SR)
    w = len(x) // n
    power = (x[: w * n] ** 2).mean(axis=1).reshape(w, n).mean(axis=1) if w else np.array([float(np.mean(x ** 2))])
    wdb = np.array([_db(p) for p in power])
    live = wdb[wdb > -90.0]                                         # hors silence numérique pur
    noise = float(np.percentile(live, 10)) if len(live) >= 4 else None
    contrast = float(np.percentile(live, 90) - np.percentile(live, 10)) if len(live) >= 4 else None
    corr = None
    if x.shape[1] == 2 and np.std(x[:, 0]) > 1e-9 and np.std(x[:, 1]) > 1e-9:
        corr = float(np.corrcoef(x[:, 0], x[:, 1])[0, 1])
    loud = measure_loudness_window(path, start, duration)
    peak = float(np.max(np.abs(x)))
    return AudioMeasure(str(path), start, len(x) / DECODE_SR, sr, ch, loud[0], loud[1],
                        None if peak <= 1e-9 else 20 * np.log10(peak), None if not len(live) else _db(float(np.mean(power))),
                        float(np.mean(np.abs(x) >= 0.999)), noise, contrast, float(np.mean(wdb < SILENCE_DB)), corr)


def measure_loudness_window(path: Path, start: float, duration: float) -> tuple[float | None, float | None]:
    """Sonie intégrée et crête vraie d'une fenêtre : on découpe un WAV temporaire (la source n'est jamais touchée)."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="kolnoa_q_") as tmp:
        wav = Path(tmp) / "w.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", str(path), "-vn",
                        "-ar", str(DECODE_SR), "-c:a", "pcm_f32le", str(wav)], check=True)
        m = measure_loudness(wav)
    return m.integrated_lufs, m.true_peak_dbtp


# --- proposition ----------------------------------------------------------------------------------------
def _lin(v: float, best: float, worst: float) -> float:
    """1 à `best`, 0 à `worst`, linéaire entre les deux (marche dans les deux sens)."""
    t = (v - worst) / (best - worst)
    return float(min(1.0, max(0.0, t)))


@dataclass
class Criterion:
    name: str
    value: float | None
    score: float
    weight: float
    note: str


@dataclass
class Ranked:
    angle: str
    score: float
    criteria: list[Criterion]
    flags: list[str]
    sync_status: str
    sync_confidence: float

    def reason(self) -> str:
        """Phrase à garder dans `MasterRange.reason` quand l'humain valide cette proposition."""
        good = [c.note for c in sorted(self.criteria, key=lambda c: -c.score * c.weight) if c.score >= 0.7][:3]
        return f"proposition validée ({self.score:.2f}) : " + "; ".join(good or ["meilleur compromis mesuré"])


@dataclass
class AudioProposal:
    group: str
    start: float
    end: float
    ranked: list[Ranked] = field(default_factory=list)
    excluded: list[tuple[str, str]] = field(default_factory=list)       # (angle, pourquoi : synchro ou mesure impossible)
    caveats: list[str] = field(default_factory=lambda: list(CAVEATS))

    @property
    def best(self) -> str | None:
        return self.ranked[0].angle if self.ranked else None

    @property
    def margin(self) -> float | None:
        return round(self.ranked[0].score - self.ranked[1].score, 4) if len(self.ranked) > 1 else None

    def explain(self) -> list[str]:
        out = []
        for i, r in enumerate(self.ranked, 1):
            out.append(f"{i}. {r.angle} : {r.score:.2f} (synchro {r.sync_status}, confiance {r.sync_confidence:.2f})")
            out += [f"     {c.name} {c.score:.2f} × {c.weight:g} : {c.note}" for c in r.criteria]
            out += [f"     ⚠ {f}" for f in r.flags]
        out += [f"écarté {a} : {why}" for a, why in self.excluded]
        if self.margin is not None and self.margin < TIE_MARGIN:
            out.append(f"écart de {self.margin:.2f} entre les deux premiers : trop faible pour trancher, à écouter")
        return out + self.caveats


def score_measure(m: AudioMeasure) -> tuple[float, list[Criterion], list[str]]:
    crit: list[Criterion] = []
    flags: list[str] = []
    s = 1.0 - min(1.0, m.clip_ratio / CLIP_ZERO_RATIO)
    crit.append(Criterion("saturation", m.clip_ratio, s, WEIGHTS["saturation"],
                          f"{m.clip_ratio * 100:.3f} % d'échantillons saturés" + (" (aucun)" if m.clip_ratio == 0 else "")))
    if m.clip_ratio > 1e-4:
        flags.append("écrêtage audible probable")
    if m.noise_floor_dbfs is not None:
        crit.append(Criterion("bruit_de_fond", m.noise_floor_dbfs, _lin(m.noise_floor_dbfs, NOISE_BEST_DB, NOISE_WORST_DB),
                              WEIGHTS["bruit_de_fond"], f"fond à {m.noise_floor_dbfs:.0f} dBFS"))
    if m.contrast_db is not None:
        crit.append(Criterion("contraste", m.contrast_db, _lin(m.contrast_db, CONTRAST_BEST_DB, CONTRAST_WORST_DB),
                              WEIGHTS["contraste"], f"{m.contrast_db:.0f} dB entre passages forts et fond"))
    crit.append(Criterion("silence", m.silence_ratio, 1.0 - min(1.0, m.silence_ratio / SILENCE_ZERO_RATIO), WEIGHTS["silence"],
                          f"{m.silence_ratio * 100:.0f} % de la séquence sous {SILENCE_DB:.0f} dBFS"))
    if m.integrated_lufs is not None:
        lo, hi = LEVEL_OK
        v = m.integrated_lufs
        lvl = 1.0 if lo <= v <= hi else (_lin(v, lo, LEVEL_ZERO[0]) if v < lo else _lin(v, hi, LEVEL_ZERO[1]))
        crit.append(Criterion("niveau", v, lvl, WEIGHTS["niveau"], f"sonie {v:.1f} LUFS"))
    if m.channel_correlation is not None:
        ph = 0.0 if m.channel_correlation < -0.3 else 1.0
        crit.append(Criterion("phase", m.channel_correlation, ph, WEIGHTS["phase"], f"corrélation des canaux {m.channel_correlation:+.2f}"))
        if ph == 0.0:
            flags.append("canaux en opposition de phase : la somme mono s'annulerait")
    if m.true_peak_dbtp is not None and m.true_peak_dbtp > -0.1:
        flags.append(f"crête vraie {m.true_peak_dbtp:.1f} dBTP : aucune marge")
    total_w = sum(c.weight for c in crit)
    return sum(c.score * c.weight for c in crit) / total_w, crit, flags


def propose_master(tl: Timeline, group_id: str, start: float, end: float, media_root: Path,
                   sources: list[str] | None = None) -> AudioProposal:
    """Mesure chaque angle audio du groupe sur la séquence [start, end] (secondes du groupe) et classe ceux dont la synchro est
    fiable. Ne modifie JAMAIS la timeline : pour appliquer, `assign_master(..., reason=proposal.ranked[0].reason())`."""
    if not 0 <= start < end:
        raise TimelineError(f"plage invalide [{start}, {end}]")
    g = tl.group(group_id)
    prop = AudioProposal(group_id, start, end)
    for a in g.angles:
        if not a.has_audio or (sources is not None and a.id not in sources):
            continue
        if not a.usable:
            prop.excluded.append((a.id, f"synchro non fiable (état « {a.status} ») : à valider avant d'être candidate"))
            continue
        t0 = max(0.0, a.to_angle(start))
        try:
            m = measure_audio(Path(media_root) / a.media, t0, min(end - start, MAX_ANALYSIS_S))
        except (TimelineError, subprocess.CalledProcessError) as exc:
            prop.excluded.append((a.id, f"mesure impossible : {exc}"))
            continue
        score, crit, flags = score_measure(m)
        prop.ranked.append(Ranked(a.id, round(score, 4), crit, flags, a.status, a.confidence))
    prop.ranked.sort(key=lambda r: -r.score)
    if not prop.ranked:
        prop.caveats.insert(0, "aucune source mesurable et synchronisée de façon fiable pour cette séquence")
    return prop
