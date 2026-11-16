"""Opérations audio de la timeline : gains, fondus, fondus enchaînés, canaux (mono / dual mono / stéréo), mute / solo,
source maître par séquence. Même principe que `editing.py` : chaque fonction renvoie une Transaction (un pas d'annulation,
tout ou rien) ; rien n'est lu sur disque ; une proposition (voir `audio_quality`) ne s'applique JAMAIS d'elle-même.

Trois sujets distincts qu'on ne confond pas :
  - la synchronisation (offset/dérive d'un angle : `Angle.status`) ;
  - le choix éditorial (quelle source on veut entendre : `MasterRange`, décidé par l'humain) ;
  - la qualité sonore mesurée (bruit, saturation, niveau : conseille le choix, ne le fait pas).
"""

from __future__ import annotations

from dataclasses import asdict

from .model import AudioStrip, Clip, MasterRange, Timeline, TimelineError, FADE_CURVES
from .ops import (AddCrossfade, AddMaster, RemoveCrossfade, RemoveMaster, SetAudioSettings, SetClipProps, SetClipStrips,
                  SetTrackProps, Transaction)

EPS = 1e-9


# --- niveaux, fondus, pistes --------------------------------------------------------------------
def set_clip_gain(tl: Timeline, clip_id: str, gain_db: float) -> SetClipProps:
    tl.clip(clip_id)
    if not -96.0 <= gain_db <= 24.0:
        raise TimelineError(f"gain {gain_db} dB hors limites (−96 à +24 dB)")
    return SetClipProps(clip_id, {"gain_db": float(gain_db)})


def set_track_audio(tl: Timeline, track_id: str, gain_db: float | None = None, muted: bool | None = None,
                    solo: bool | None = None) -> SetTrackProps:
    tl.track(track_id)
    props = {k: v for k, v in (("gain_db", gain_db), ("muted", muted), ("solo", solo)) if v is not None}
    if not props:
        raise TimelineError("rien à modifier")
    if "gain_db" in props and not -96.0 <= props["gain_db"] <= 24.0:
        raise TimelineError(f"gain {gain_db} dB hors limites (−96 à +24 dB)")
    return SetTrackProps(track_id, props)


def set_fades(tl: Timeline, clip_id: str, fade_in: float | None = None, fade_out: float | None = None,
              in_curve: str | None = None, out_curve: str | None = None) -> SetClipProps:
    """Fondus d'entrée / sortie d'un plan (secondes) et leur courbe. Un plan qui touche un fondu enchaîné n'a pas de fondu
    propre de ce côté : le fondu enchaîné le remplace."""
    c = tl.clip(clip_id)
    props: dict = {}
    for key, val in (("fade_in", fade_in), ("fade_out", fade_out), ("fade_in_curve", in_curve), ("fade_out_curve", out_curve)):
        if val is not None:
            props[key] = val
    if not props:
        raise TimelineError("rien à modifier")
    for k in ("fade_in_curve", "fade_out_curve"):
        if k in props and props[k] not in FADE_CURVES:
            raise TimelineError(f"courbe inconnue : {props[k]!r} (valeurs : {', '.join(sorted(FADE_CURVES))})")
    fi, fo = props.get("fade_in", c.fade_in), props.get("fade_out", c.fade_out)
    if fi < 0 or fo < 0 or fi + fo > c.duration / tl.fps + EPS:
        raise TimelineError(f"fondus invalides pour {c.id} (plan de {c.duration / tl.fps:.3f} s)")
    for x in tl.crossfades:
        if "fade_in" in props and props["fade_in"] > 0 and x.right == c.id:
            raise TimelineError(f"{c.id} est le côté droit du fondu enchaîné {x.id} : supprimez-le avant d'ajouter un fondu d'entrée")
        if "fade_out" in props and props["fade_out"] > 0 and x.left == c.id:
            raise TimelineError(f"{c.id} est le côté gauche du fondu enchaîné {x.id} : supprimez-le avant d'ajouter un fondu de sortie")
    return SetClipProps(clip_id, props)


# --- canaux : mono, dual mono, stéréo -----------------------------------------------------------
def _check_src(src: int, channels: int | None) -> None:
    if src < 0 or (channels is not None and src >= channels):
        raise TimelineError(f"canal source {src} inexistant" + (f" (la source a {channels} canal/canaux)" if channels else ""))


def to_mono(tl: Timeline, clip_id: str, src: int = 0, channels: int | None = None) -> SetClipStrips:
    """Un seul canal de sortie, au centre (le canal source `src`)."""
    tl.clip(clip_id)
    _check_src(src, channels)
    return SetClipStrips(clip_id, [asdict(AudioStrip(src=src, pan=0.0, name="mono"))])


def to_dual_mono(tl: Timeline, clip_id: str, left: int = 0, right: int | None = None, channels: int | None = None) -> SetClipStrips:
    """Deux canaux INDÉPENDANTS (gauche dur / droite dur), chacun avec son gain, son muet et son solo.
    `right=None` : même canal source des deux côtés (mono réparti en deux canaux réglables séparément).
    `left=0, right=1` sur un fichier stéréo où chaque canal est un micro différent : on les traite comme deux monos."""
    tl.clip(clip_id)
    r = left if right is None else right
    _check_src(left, channels)
    _check_src(r, channels)
    return SetClipStrips(clip_id, [asdict(AudioStrip(src=left, pan=-1.0, name="G")), asdict(AudioStrip(src=r, pan=1.0, name="D"))])


def to_stereo(tl: Timeline, clip_id: str, channels: int | None = 2) -> SetClipStrips:
    """Stéréo vraie : canal 0 à gauche, canal 1 à droite."""
    tl.clip(clip_id)
    _check_src(1, channels)
    return SetClipStrips(clip_id, [asdict(AudioStrip(src=0, pan=-1.0, name="G")), asdict(AudioStrip(src=1, pan=1.0, name="D"))])


def reset_channels(tl: Timeline, clip_id: str) -> SetClipStrips:
    """Revient au routage d'origine de la source."""
    tl.clip(clip_id)
    return SetClipStrips(clip_id, None)


def set_strip(tl: Timeline, clip_id: str, index: int, gain_db: float | None = None, pan: float | None = None,
              mute: bool | None = None, solo: bool | None = None, src: int | None = None, channels: int | None = None) -> SetClipStrips:
    """Règle un canal de sortie : gain, panoramique, muet, solo, canal source."""
    c = tl.clip(clip_id)
    if c.strips is None:
        raise TimelineError(f"{clip_id} n'a pas de canaux explicites : utiliser to_mono / to_dual_mono / to_stereo d'abord")
    if not 0 <= index < len(c.strips):
        raise TimelineError(f"{clip_id} n'a pas de canal {index}")
    strips = [asdict(b) for b in c.strips]
    for k, v in (("gain_db", gain_db), ("pan", pan), ("mute", mute), ("solo", solo), ("src", src)):
        if v is not None:
            strips[index][k] = v
    if src is not None:
        _check_src(src, channels)
    return SetClipStrips(clip_id, strips)


# --- fondus enchaînés ---------------------------------------------------------------------------
def add_crossfade(tl: Timeline, left_id: str, right_id: str, duration: float = 0.5, curve: str = "equal_power",
                  source_len: dict[str, float] | None = None) -> AddCrossfade:
    """Fondu enchaîné centré sur la coupure entre deux plans voisins d'une même piste.
    `duration` = durée totale (la moitié de chaque côté). `source_len` {média: secondes}, si connu, permet de vérifier
    qu'il reste de la matière après la sortie du plan de gauche. `equal_power` pour deux sources différentes (deux prises
    de son), `linear` pour une même source coupée."""
    left, right = tl.clip(left_id), tl.clip(right_id)
    if left.track != right.track or left.end != right.start:
        raise TimelineError(f"{left_id} et {right_id} ne sont pas voisins sur la même piste")
    if curve not in FADE_CURVES:
        raise TimelineError(f"courbe inconnue : {curve!r}")
    if not duration > 0:
        raise TimelineError("durée de fondu enchaîné invalide")
    half = duration / 2
    if half > left.duration / tl.fps + EPS or half > right.duration / tl.fps + EPS:
        raise TimelineError("le fondu enchaîné est plus long que la moitié d'un des plans")
    if right.src_in + EPS < half:
        raise TimelineError(f"{right_id} n'a que {right.src_in:.3f} s de matière avant son entrée : il en faut {half:.3f} s")
    if source_len and left.media in source_len:
        tail = source_len[left.media] - (left.src_in + left.duration / tl.fps)
        if tail + EPS < half:
            raise TimelineError(f"{left_id} n'a que {max(tail, 0):.3f} s de matière après sa sortie : il en faut {half:.3f} s")
    if left.fade_out > 0 or right.fade_in > 0:
        raise TimelineError("remettre à zéro le fondu de sortie du plan de gauche et d'entrée du plan de droite d'abord")
    for x in tl.crossfades:
        if {x.left, x.right} & {left_id, right_id} and (x.left == left_id or x.right == right_id):
            raise TimelineError(f"un fondu enchaîné existe déjà ici ({x.id})")
    return AddCrossfade({"id": tl.next_crossfade_id(), "left": left_id, "right": right_id,
                         "duration": float(duration), "curve": curve})


def remove_crossfade(tl: Timeline, xf_id: str) -> RemoveCrossfade:
    tl.crossfade(xf_id)
    return RemoveCrossfade(xf_id)


# --- source maître par séquence ------------------------------------------------------------------
def master_at(tl: Timeline, group_id: str, t_group: float) -> str:
    """Angle dont l'audio est entendu à l'instant `t_group` (axe du groupe) : plage éventuelle, sinon maître du groupe."""
    g = tl.group(group_id)
    for r in tl.masters:
        if r.group == group_id and r.start - EPS <= t_group < r.end - EPS:
            return r.source
    return g.master


def _usable_audio(tl: Timeline, group_id: str, source: str):
    g = tl.group(group_id)
    a = g.angle(source)
    if not a.has_audio:
        raise TimelineError(f"l'angle {g.id}/{a.id} n'a pas d'audio")
    if not a.usable:
        raise TimelineError(f"l'angle {g.id}/{a.id} n'est pas synchronisé de façon fiable (état « {a.status} ») : "
                            "valider ou aligner à la main avant de s'en servir comme source audio")
    return a


def assign_master(tl: Timeline, group_id: str, start: float, end: float, source: str, reason: str = "") -> Transaction:
    """Pour la séquence [start, end] (secondes du groupe), le son vient de `source`. Les plages déjà posées qui chevauchent
    sont raccourcies ou coupées (rien d'autre ne bouge). Refuse une source non synchronisée de façon fiable."""
    if not 0 <= start < end:
        raise TimelineError(f"plage invalide [{start}, {end}]")
    _usable_audio(tl, group_id, source)
    ops = _carve(tl, group_id, start, end)
    nid = _IdPool(tl)
    ops.append(AddMaster(asdict(MasterRange(nid.next(), group_id, round(start, 6), round(end, 6), source, reason))))
    return Transaction(_renumber(tl, ops), f"source audio {source} de {start:.2f} s à {end:.2f} s")


def clear_master(tl: Timeline, group_id: str, start: float, end: float) -> Transaction:
    """Rend la séquence [start, end] au maître du groupe."""
    tl.group(group_id)
    if not 0 <= start < end:
        raise TimelineError(f"plage invalide [{start}, {end}]")
    ops = _carve(tl, group_id, start, end)
    if not ops:
        raise TimelineError("aucune plage de source audio à cet endroit")
    return Transaction(_renumber(tl, ops), f"maître du groupe de {start:.2f} s à {end:.2f} s")


class _IdPool:
    def __init__(self, tl: Timeline):
        self.taken = {r.id for r in tl.masters}
        self.n = 0

    def next(self) -> str:
        n = 1
        while f"r{n:03d}" in self.taken:
            n += 1
        rid = f"r{n:03d}"
        self.taken.add(rid)
        return rid


def _carve(tl: Timeline, group_id: str, start: float, end: float) -> list:
    """Opérations qui libèrent [start, end] : supprime ou raccourcit les plages touchées, en gardant leurs restes."""
    pool = _IdPool(tl)
    ops: list = []
    for r in sorted((x for x in tl.masters if x.group == group_id), key=lambda x: x.start):
        if r.end <= start + EPS or r.start >= end - EPS:
            continue
        ops.append(RemoveMaster(r.id))
        pool.taken.discard(r.id)
        if r.start < start - EPS:
            ops.append(AddMaster(asdict(MasterRange(r.id, r.group, r.start, round(start, 6), r.source, r.reason))))
            pool.taken.add(r.id)
        if r.end > end + EPS:
            ops.append(AddMaster(asdict(MasterRange(pool.next(), r.group, round(end, 6), r.end, r.source, r.reason))))
    return ops


def _renumber(tl: Timeline, ops: list) -> list:
    """Garantit des ids de plage distincts dans une même transaction (les restes et la nouvelle plage)."""
    used = {r.id for r in tl.masters}
    seen: set[str] = set()
    out = []
    for op in ops:
        if isinstance(op, AddMaster):
            rid = op.master["id"]
            if rid in seen:
                n = 1
                while f"r{n:03d}" in used or f"r{n:03d}" in seen:
                    n += 1
                op = AddMaster({**op.master, "id": f"r{n:03d}"})
                rid = op.master["id"]
            seen.add(rid)
        out.append(op)
    return out


def set_audio_settings(tl: Timeline, **props) -> SetAudioSettings:
    """Fréquence de mixage, plafond de crête (dBTP), cible de sonie (LUFS, None = aucune), fondu de changement de source (ms)."""
    if not props:
        raise TimelineError("rien à modifier")
    return SetAudioSettings(props)
