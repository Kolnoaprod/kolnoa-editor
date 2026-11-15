"""Synchronisation visuelle ASSISTÉE et EXPÉRIMENTALE (drone sans audio).

Idée : repérer des événements de lumière communs (flash photo, lumières de piste qui s'allument, entrée dans la
salle, soleil/nuage) dans la caméra de référence et dans le drone. Un drone en vue aérienne et une caméra serrée ne
voient pas la même chose : la correspondance peut être impossible. Ce module donc :
  - ne produit que des PROPOSITIONS classées, avec une confiance explicite plafonnée (jamais « ok ») ;
  - dit « ambigu » quand deux alignements se valent, « impossible » quand il manque des événements ;
  - ne modifie jamais un angle : c'est l'humain qui valide (`accept_angle`) ou règle image par image.
Précision attendue : une fraction de seconde (cadence d'analyse). Le réglage final est manuel.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..ffmpeg import CancelToken, ffmpeg_bin, run
from ..timeline.model import Timeline
from ..timeline.ops import SetAngleSync
from .config import SyncConfig
from .correlate import ncc_curve

W, H = 32, 18        # miniature d'analyse


@dataclass
class LumaSeries:
    """Luminosité moyenne par image d'analyse (cadence `fps`) ; `t0` = instant du premier échantillon (s)."""
    luma: np.ndarray
    fps: float
    t0: float = 0.0

    @property
    def duration(self) -> float:
        return len(self.luma) / self.fps


@dataclass
class VisualCandidate:
    offset: float          # t_groupe - t_angle proposé (s)
    confidence: float      # plafonnée par visual_max_confidence
    score: float           # corrélation normalisée du pic
    psr: float
    matched_events: int
    rank: int = 1
    p_chance: float = 1.0   # probabilité que ces concordances soient dues au hasard (corrigée du nombre de décalages testés)


@dataclass
class VisualResult:
    status: str                                   # 'proposition' | 'faible' | 'ambigu' | 'impossible'
    candidates: list[VisualCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    events_ref: int = 0
    events_sig: int = 0
    experimental: bool = True


def luma_series(path: Path | str, cfg: SyncConfig | None = None, cancel: CancelToken | None = None,
                tmp_dir: Path | None = None) -> LumaSeries:
    """Série de luminosité moyenne d'une vidéo, via ffmpeg (miniatures 32×18 en niveaux de gris)."""
    cfg = cfg or SyncConfig()
    fd, name = tempfile.mkstemp(suffix=".gray", dir=tmp_dir)
    os.close(fd)
    out = Path(name)
    try:
        run([ffmpeg_bin(), "-y", "-hide_banner", "-v", "error", "-i", str(path), "-an",
             "-vf", f"fps={cfg.visual_fps},scale={W}:{H}:flags=area,format=gray", "-f", "rawvideo", str(out)],
            cancel=cancel)
        raw = np.fromfile(out, dtype=np.uint8)
    finally:
        out.unlink(missing_ok=True)
    frames = raw[: (len(raw) // (W * H)) * W * H].reshape(-1, W * H)
    return LumaSeries(frames.mean(axis=1).astype(np.float64), cfg.visual_fps)


def _events(series: LumaSeries, cfg: SyncConfig) -> tuple[np.ndarray, np.ndarray]:
    """(signal d'événements, indices des événements marquants). Signal = |variation de luminosité| en écarts-types
    robustes, un peu étalé dans le temps pour tolérer quelques dixièmes de seconde d'écart."""
    x = series.luma
    if len(x) < 8:
        return np.zeros(len(x)), np.zeros(0, dtype=int)
    d = np.abs(np.diff(x, prepend=x[0]))
    med = float(np.median(d))
    mad = float(np.median(np.abs(d - med))) * 1.4826
    scale = max(mad, 0.5)                          # plancher : le bruit de compression n'est pas un événement
    z = np.clip((d - med) / scale, 0.0, 12.0)
    z = np.where(d >= cfg.visual_event_min_delta, z, 0.0)       # sous ce saut de luminosité : bruit, pas un événement
    idx = np.flatnonzero(z >= cfg.visual_event_z)
    k = max(1, int(round(0.3 * series.fps)))      # étalement ≈ 0,3 s
    kernel = np.hanning(2 * k + 1)
    sig = np.convolve(z, kernel / kernel.sum(), mode="same")
    return sig, idx


def propose_visual_sync(ref: LumaSeries, sig: LumaSeries, cfg: SyncConfig | None = None,
                        hint: tuple[float, float] | None = None) -> VisualResult:
    """Propose des alignements du drone (`sig`) sur la caméra de référence (`ref`), sans jamais les appliquer."""
    cfg = cfg or SyncConfig()
    if abs(ref.fps - sig.fps) > 1e-6:
        raise ValueError("séries à des cadences différentes")
    fps = ref.fps
    e_ref, i_ref = _events(ref, cfg)
    e_sig, i_sig = _events(sig, cfg)
    res = VisualResult("impossible", events_ref=len(i_ref), events_sig=len(i_sig))
    if len(i_ref) < cfg.visual_min_events or len(i_sig) < cfg.visual_min_events:
        res.notes.append(f"pas assez d'événements lumineux marquants ({len(i_ref)} côté référence, {len(i_sig)} côté "
                         f"drone, {cfg.visual_min_events} requis) : vues trop différentes ou scène trop stable. "
                         "Proposer un alignement manuel côte à côte avec deux repères.")
        return res
    curve = ncc_curve(e_ref, e_sig)
    if curve is None:
        res.notes.append("séries inexploitables")
        return res
    cc, lags0, lo, hi = curve
    if hint is not None:                                    # indice métadonnées : restreint la recherche (en secondes)
        centre = int(round(hint[0] * fps)) - lags0
        w = int(round(hint[1] * fps))
        lo, hi = max(lo, centre - w), min(hi, centre + w + 1)
    if hi - lo < 8:
        res.notes.append("plage de recherche trop étroite")
        return res
    seg = cc[lo:hi]
    mean, sd = float(seg.mean()), float(seg.std())
    if sd < 1e-9:
        res.notes.append("corrélation plate")
        return res
    # pics locaux, séparés d'au moins 3 s, classés par hauteur
    order = np.argsort(seg)[::-1]
    sep = int(3 * fps)
    chosen: list[int] = []
    for i in order:
        if all(abs(int(i) - j) >= sep for j in chosen):
            chosen.append(int(i))
        if len(chosen) >= cfg.visual_top_k:
            break
    ev_ref_t = i_ref / fps
    cands: list[VisualCandidate] = []
    for rank, i in enumerate(chosen, start=1):
        lag = (lo + i + lags0)
        # interpolation parabolique
        frac = 0.0
        if 0 < i < len(seg) - 1:
            a, b, c = float(seg[i - 1]), float(seg[i]), float(seg[i + 1])
            den = a - 2 * b + c
            if abs(den) > 1e-18:
                frac = 0.5 * (a - c) / den
        offset = (lag + frac) / fps + (ref.t0 - sig.t0)
        shifted = i_sig / fps + (lag + frac) / fps
        matched = int(sum(np.min(np.abs(ev_ref_t - t)) <= cfg.visual_match_s for t in shifted)) if len(ev_ref_t) else 0
        psr = (float(seg[i]) - mean) / sd
        c_psr = float(np.clip((psr - cfg.visual_min_psr) / max(cfg.visual_good_psr - cfg.visual_min_psr, 1e-9), 0, 1))
        c_ev = min(1.0, matched / 4.0)
        # significativité : combien de concordances le hasard donnerait-il, vu la densité d'événements et l'étendue testée ?
        t_lo, t_hi = ref.t0 - sig.t0 - 0.0, ref.t0 - sig.t0 + ref.duration
        overlap = int(sum(0.0 <= t - (ref.t0 - sig.t0) <= ref.duration for t in (shifted + (ref.t0 - sig.t0))))
        del t_lo, t_hi
        lam = max(overlap, matched) * 2 * cfg.visual_match_s * (len(i_ref) / max(ref.duration, 1e-9))
        p_raw = 1.0 - sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(matched))
        n_lags = max(1.0, ((hi - lo) / fps) / (2 * cfg.visual_match_s))
        p_chance = float(min(1.0, max(p_raw, 0.0) * n_lags))
        c_sig = float(np.clip(-math.log10(max(p_chance, 1e-12)) / 2.0, 0.0, 1.0))
        cands.append(VisualCandidate(offset, 0.0, float(seg[i]), psr, matched, rank, p_chance))
        cands[-1].confidence = round(cfg.visual_max_confidence *
                                     (max(c_psr, 0.02) * max(c_ev, 0.02) * max(c_sig, 0.02)) ** (1 / 3), 4)
    best = cands[0]
    res.candidates = cands
    if best.psr < cfg.visual_min_psr or best.matched_events < cfg.visual_min_events or best.p_chance > cfg.visual_weak_chance_p:
        res.notes.append(f"aucun alignement ne se détache (pic/bruit {best.psr:.1f}, {best.matched_events} "
                         f"événement(s) concordant(s), risque de coïncidence {best.p_chance:.2f}) : correspondance "
                         "visuelle impossible ou non démontrée. Proposer un alignement manuel côte à côte.")
        for c in cands:
            c.confidence = min(c.confidence, 0.1)
        return res
    if best.p_chance > cfg.visual_max_chance_p:
        res.status = "faible"
        res.notes.append(f"indice faible : {best.matched_events} événement(s) concordant(s), risque de coïncidence "
                         f"{best.p_chance:.2f}. À vérifier côte à côte avant toute validation.")
        for c in cands:
            c.confidence = min(c.confidence, 0.25)
        return res
    res.candidates = cands
    if len(cands) > 1 and cands[1].score >= cfg.visual_ambiguity_ratio * cands[0].score:
        res.status = "ambigu"
        res.notes.append("plusieurs alignements aussi plausibles (événements répétitifs ?) : choisir à la main")
        for c in cands:
            c.confidence = min(c.confidence, 0.3)
    else:
        res.status = "proposition"
    res.notes.append("EXPÉRIMENTAL : à valider côte à côte, puis régler image par image")
    return res


def accept_candidate(tl: Timeline, group_id: str, angle_id: str, cand: VisualCandidate) -> SetAngleSync:
    """Pose une proposition visuelle sur l'angle, sans la déclarer fiable : état « incertain », méthode « visual »,
    confiance plafonnée. Elle ne devient utilisable qu'après validation humaine (`accept_angle`) ou réglage manuel."""
    tl.group(group_id).angle(angle_id)
    return SetAngleSync(group_id, angle_id, {
        "offset": round(cand.offset, 6), "drift_ppm": 0.0, "status": "incertain", "method": "visual",
        "confidence": cand.confidence, "note": f"EXPÉRIMENTAL : proposition visuelle #{cand.rank}, "
                                               f"{cand.matched_events} événement(s) concordant(s)"})
