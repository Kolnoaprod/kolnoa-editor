"""Export ANONYMISÉ d'une timeline (jeu de test public). Principe : LISTE BLANCHE, jamais « copier puis effacer ».

Le document exporté est CONSTRUIT champ par champ à partir de `FIELD_POLICY` : ce qui n'y figure pas n'est pas exporté, et un
test échoue si un champ est ajouté au modèle sans y recevoir une décision (la confidentialité n'est jamais supposée par défaut).
Toute chaîne exportée est soit un identifiant GÉNÉRÉ (`s001`, `rush-001.mp4`, `V1`…), soit une valeur d'une énumération connue.
`lint_export` vérifie le document FINAL de façon récursive : clé hors liste blanche, chaîne libre ou terme interdit (`deny`) ⇒ refus,
rien n'est écrit. Les textes de chapitres sont assainis : seuls des types de séquence génériques passent ; le reste devient
`section-N` (N = ordre de première apparition) sauf correspondance fournie explicitement par le réalisateur.

Limite assumée : une liste blanche garantit la STRUCTURE, pas l'absence de toute information indirecte (une durée ou une cadence
inhabituelle peut rappeler un film). Le fichier doit être relu par le réalisateur avant tout partage ; voir docs/DIRECTOR_MEMORY.md."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterable

from ...timeline import Timeline
from ...timeline.model import (AUDIO_MODES, FADE_CURVES, MARKER_KINDS, SYNC_METHODS, SYNC_STATUSES, TRACK_KINDS, Angle,
                               AudioSettings, AudioStrip, Clip, Crossfade, MasterRange, Marker, MulticamGroup, Subclip, Track)

K, M, D = "keep", "mapped", "drop"          # conservé (valeur validée) | remplacé par un identifiant généré | non exporté
FIELD_POLICY: dict[str, dict[str, str]] = {
    "Timeline": {"title": M, "version": K, "fps": K, "resolution": K, "source_gain_db": K, "music": "filtered", "tracks": "rebuilt",
                 "clips": "rebuilt", "markers": "rebuilt", "groups": "rebuilt", "masters": "rebuilt", "crossfades": "rebuilt",
                 "audio_settings": "rebuilt", "bin": D, "retired": D, "schema": K},
    "Track": {"id": M, "kind": "enum", "role": "enum", "locked": K, "muted": K, "gain_db": K, "solo": K},
    "Clip": {"id": M, "track": M, "media": M, "start": K, "duration": K, "src_in": K, "audio": "enum", "fade_in": K, "fade_out": K,
             "gain_db": K, "note": D, "label": D, "locked": K, "parent": M, "group": M, "angle": M, "link": M, "strips": "rebuilt",
             "fade_in_curve": "enum", "fade_out_curve": "enum", "extra": D},
    "AudioStrip": {"src": K, "gain_db": K, "pan": K, "mute": K, "solo": K, "name": D},
    "Marker": {"id": M, "frame": K, "kind": "enum", "text": "chapter"},
    "Subclip": {f.name: D for f in fields(Subclip)},                       # chutier : noms et étiquettes libres, non exporté en v0
    "Angle": {"id": M, "media": M, "kind": "enum", "has_audio": K, "offset": K, "drift_ppm": K, "confidence": K, "status": "enum",
              "method": "enum", "locked": K, "points": "numbers", "range": "numbers", "note": D},
    "MulticamGroup": {"id": M, "master": M, "name": M, "angles": "rebuilt"},
    "MasterRange": {"id": M, "group": M, "start": K, "end": K, "source": M, "reason": D},
    "Crossfade": {"id": M, "left": M, "right": M, "duration": K, "curve": "enum"},
    "AudioSettings": {"sample_rate": K, "ceiling_dbtp": K, "target_lufs": K, "switch_fade_ms": K},
}
_CLASSES = {"Timeline": Timeline, "Track": Track, "Clip": Clip, "AudioStrip": AudioStrip, "Marker": Marker, "Subclip": Subclip,
            "Angle": Angle, "MulticamGroup": MulticamGroup, "MasterRange": MasterRange, "Crossfade": Crossfade,
            "AudioSettings": AudioSettings}

ROLES = {"main", "ambiance", "voix", "musique", "effets", "coupe", "cutaway", "insert"}
GENERIC_SECTIONS = {"preparatifs", "mairie", "ceremonie", "cocktail", "reception", "danses", "discours", "entree", "soiree",
                    "introduction", "conclusion", "teaser"}
_ENUMS = ROLES | AUDIO_MODES | MARKER_KINDS | FADE_CURVES | SYNC_STATUSES | SYNC_METHODS | TRACK_KINDS | GENERIC_SECTIONS | {"", "autre"}
_GENERATED = [re.compile(p) for p in (r"^[a-z]\d{3,}$", r"^[VA]\d+$", r"^rush-\d{3,}\.mp4$", r"^musique\.wav$", r"^Projet anonymisé$",
                                       r"^groupe \d+$", r"^angle\d+$", r"^section-\d+$")]
_MUSIC_KEYS = {"path", "start", "gain_db", "fade_out"}
_ALLOWED_KEYS = {k for p in FIELD_POLICY.values() for k in p} | _MUSIC_KEYS
_NUM = (int, float)


class ExportRefused(ValueError):
    """Le document exporté ne passe pas le contrôle de confidentialité : rien n'est écrit."""


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _num(v: Any, default: float = 0.0):
    return v if isinstance(v, _NUM) and not isinstance(v, bool) else default


def _enum(v: Any, allowed: Iterable[str], default: str) -> str:
    return v if isinstance(v, str) and v in set(allowed) else default


def _numbers(v: Any):
    """Listes de nombres seulement (aucune chaîne ne passe)."""
    if isinstance(v, (list, tuple)):
        return [_numbers(x) if isinstance(x, (list, tuple)) else _num(x) for x in v]
    return None


class _Map:
    def __init__(self, fmt: str):
        self.fmt, self.m = fmt, {}

    def __call__(self, key):
        if key is None:
            return None
        return self.m.setdefault(key, self.fmt.format(len(self.m) + 1))


def export_timeline(tl: Timeline, chapter_labels: dict[str, str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Construit le document anonymisé (dictionnaire) par liste blanche. `tl` n'est jamais modifiée."""
    src = tl.to_dict()
    media, tracks, clips, links = _Map("rush-{:03d}.mp4"), {}, _Map("s{:03d}"), _Map("l{:03d}")
    markers, groups, masters, xfades = _Map("m{:03d}"), _Map("g{:03d}"), _Map("r{:03d}"), _Map("x{:03d}")
    counts = {"V": 0, "A": 0}
    for t in src["tracks"]:
        k = "A" if t.get("kind") == "audio" else "V"
        counts[k] += 1
        tracks[t["id"]] = f"{k}{counts[k]}"
    dropped = {"bin": len(src.get("bin", [])), "retired": len(src.get("retired", [])), "clip_texts": 0, "music_keys": 0}
    labels, chapter_map = dict(chapter_labels or {}), {}

    def chapter(text: str) -> str:
        if text in chapter_map:
            return chapter_map[text]
        if text in labels:
            out = labels[text]
        elif _norm(text.strip()) in GENERIC_SECTIONS:
            out = _norm(text.strip())
        else:
            out = f"section-{sum(1 for v in chapter_map.values() if v.startswith('section-')) + 1}"
        chapter_map[text] = out
        return out

    out: dict[str, Any] = {"title": "Projet anonymisé", "version": int(_num(src.get("version"), 1)), "fps": int(_num(src["fps"], 25)),
                           "resolution": [int(_num(x, 1)) for x in src["resolution"]],
                           "source_gain_db": _num(src.get("source_gain_db")), "schema": src["schema"]}
    music = src.get("music")
    if isinstance(music, dict):
        out["music"] = {"path": "musique.wav"} if music.get("path") else {}
        for key in ("start", "gain_db", "fade_out"):
            if isinstance(music.get(key), _NUM):
                out["music"][key] = music[key]
        dropped["music_keys"] = len([k for k in music if k not in ("path", "start", "gain_db", "fade_out")])
    else:
        out["music"] = None
    out["tracks"] = [{"id": tracks[t["id"]], "kind": _enum(t.get("kind"), TRACK_KINDS, "video"), "role": _enum(t.get("role"), ROLES, "autre"),
                      "locked": bool(t.get("locked")), "muted": bool(t.get("muted")), "gain_db": _num(t.get("gain_db")),
                      "solo": bool(t.get("solo"))} for t in src["tracks"]]
    amaps: dict[str, dict[str, str]] = {}
    out["groups"] = []
    for gi, g in enumerate(src["groups"], 1):
        am = {a["id"]: f"angle{n}" for n, a in enumerate(g["angles"], 1)}
        amaps[g["id"]] = am
        out["groups"].append({"id": groups(g["id"]), "master": am.get(g["master"], "angle1"), "name": f"groupe {gi}", "angles": [
            {"id": am[a["id"]], "media": media(a["media"]), "kind": _enum(a.get("kind"), TRACK_KINDS, "video"),
             "has_audio": bool(a.get("has_audio")), "offset": _num(a.get("offset")), "drift_ppm": _num(a.get("drift_ppm")),
             "confidence": _num(a.get("confidence")), "status": _enum(a.get("status"), SYNC_STATUSES, "unsynced"),
             "method": _enum(a.get("method"), SYNC_METHODS, "none"), "locked": bool(a.get("locked")),
             "points": _numbers(a.get("points")) or [], "range": _numbers(a.get("range")), "note": ""} for a in g["angles"]]})
    out["clips"] = []
    for c in src["clips"]:
        dropped["clip_texts"] += bool(c.get("note")) + bool(c.get("label")) + bool(c.get("extra"))
        strips = None if c.get("strips") is None else [{"src": int(_num(s.get("src"))), "gain_db": _num(s.get("gain_db")),
                                                        "pan": _num(s.get("pan")), "mute": bool(s.get("mute")), "solo": bool(s.get("solo")),
                                                        "name": ""} for s in c["strips"]]
        out["clips"].append({
            "id": clips(c["id"]), "track": tracks[c["track"]], "media": media(c["media"]), "start": int(_num(c["start"])),
            "duration": int(_num(c["duration"])), "src_in": _num(c.get("src_in")), "audio": _enum(c.get("audio"), AUDIO_MODES, "source"),
            "fade_in": _num(c.get("fade_in")), "fade_out": _num(c.get("fade_out")), "gain_db": _num(c.get("gain_db")),
            "note": "", "label": "", "locked": bool(c.get("locked")), "parent": None, "group": groups(c["group"]) if c.get("group") else None,
            "angle": amaps.get(c.get("group"), {}).get(c.get("angle")) if c.get("group") else None,
            "link": links(c["link"]) if c.get("link") else None, "strips": strips,
            "fade_in_curve": _enum(c.get("fade_in_curve"), FADE_CURVES, "linear"),
            "fade_out_curve": _enum(c.get("fade_out_curve"), FADE_CURVES, "linear"), "extra": {}})
    for o, c in zip(out["clips"], src["clips"]):          # filiation : seulement vers un plan exporté
        o["parent"] = clips.m.get(c.get("parent")) if c.get("parent") in clips.m else None
    out["markers"] = [{"id": markers(m["id"]), "frame": int(_num(m["frame"])), "kind": _enum(m.get("kind"), MARKER_KINDS, "note"),
                       "text": chapter(str(m.get("text", ""))) if m.get("kind") == "chapter" else ""} for m in src["markers"]]
    out["masters"] = [{"id": masters(r["id"]), "group": groups(r["group"]), "start": _num(r["start"]), "end": _num(r["end"]),
                       "source": amaps.get(r["group"], {}).get(r["source"], "angle1"), "reason": ""} for r in src["masters"]]
    out["crossfades"] = [{"id": xfades(x["id"]), "left": clips.m.get(x["left"], "s000"), "right": clips.m.get(x["right"], "s000"),
                          "duration": _num(x["duration"]), "curve": _enum(x.get("curve"), FADE_CURVES, "equal_power")}
                         for x in src["crossfades"]]
    st = src.get("audio_settings", {})
    out["audio_settings"] = {"sample_rate": int(_num(st.get("sample_rate"), 48000)), "ceiling_dbtp": _num(st.get("ceiling_dbtp"), -1.0),
                             "target_lufs": st.get("target_lufs") if isinstance(st.get("target_lufs"), _NUM) else None,
                             "switch_fade_ms": _num(st.get("switch_fade_ms"), 10.0)}
    out["bin"], out["retired"] = [], []
    n_sections = len({v for v in chapter_map.values() if v.startswith("section-")})
    report = {"n_media": len(media.m), "chapters_total": len(chapter_map), "chapters_replaced_by_section_n": n_sections,
              "chapters_generic_kept": len([v for v in chapter_map.values() if v in GENERIC_SECTIONS]),
              "chapters_user_labels": len([k for k in chapter_map if k in labels]), "dropped": dropped}
    return out, report


def lint_export(doc: Any, deny: Iterable[str] = (), allowed_extra: Iterable[str] = ()) -> list[str]:
    """Contrôle RÉCURSIF du document final. Renvoie la liste des violations (vide = acceptable)."""
    extra, problems = set(allowed_extra), []

    def walk(x: Any, path: str):
        if isinstance(x, dict):
            for k, v in x.items():
                if k not in _ALLOWED_KEYS:
                    problems.append(f"{path}/{k}: clé hors liste blanche")
                walk(v, f"{path}/{k}")
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
        elif isinstance(x, str):
            if x not in _ENUMS and x not in extra and not any(p.match(x) for p in _GENERATED):
                problems.append(f"{path}: chaîne libre non autorisée")

    walk(doc, "")
    text = _norm(json.dumps(doc, ensure_ascii=False))
    for term in deny:
        if term.strip() and _norm(term.strip()) in text:
            problems.append(f"terme interdit présent : (masqué, {len(term.strip())} caractères)")
    return problems


def anonymize_timeline(tl: Timeline, chapter_labels: dict[str, str] | None = None,
                       deny: Iterable[str] = ()) -> tuple[Timeline, dict[str, Any]]:
    """Exporte, valide (le résultat est une timeline valide), contrôle (`lint_export`) ; lève `ExportRefused` sinon."""
    doc, report = export_timeline(tl, chapter_labels)
    problems = lint_export(doc, deny, allowed_extra=(chapter_labels or {}).values())
    if problems:
        raise ExportRefused("export refusé : " + "; ".join(problems[:10]))
    out = Timeline.from_dict(doc)
    out.validate()
    return out, report


# --- écriture : jamais dans une session, jamais d'écrasement implicite ---------------------------------------------------
_SESSION_FILES = ("base.json", "history.jsonl", "current.json")


def _is_session_dir(p: Path) -> bool:
    return any((p / n).exists() for n in _SESSION_FILES)


def safe_output_path(source: Path, out: Path, force: bool = False) -> Path:
    """Valide le chemin de sortie ; lève `ExportRefused`. Les originaux et les dossiers de session restent intacts."""
    source, out = Path(source), Path(out)
    if out.is_symlink():
        raise ExportRefused("sortie refusée : lien symbolique")
    out_r = out.resolve()
    if out_r.name in _SESSION_FILES:
        raise ExportRefused("sortie refusée : nom réservé aux fichiers de session")
    src_dir = (source if source.is_dir() else source.parent).resolve()
    src_file = (source / "current.json" if source.is_dir() else source).resolve()
    if out_r == src_file:
        raise ExportRefused("sortie refusée : identique au fichier source")
    if source.is_dir() or _is_session_dir(src_dir):
        if out_r == src_dir or src_dir in out_r.parents:
            raise ExportRefused("sortie refusée : à l'intérieur du dossier de session source")
    for parent in out_r.parents:
        if _is_session_dir(parent):
            raise ExportRefused(f"sortie refusée : dans un dossier de session ({parent.name})")
    if not out_r.parent.is_dir():
        raise ExportRefused("sortie refusée : le dossier de destination n'existe pas")
    if out_r.exists() and not force:
        raise ExportRefused("sortie refusée : le fichier existe déjà (--force pour l'écraser explicitement)")
    return out_r


def write_export(doc_tl: Timeline, source: Path, out: Path, force: bool = False, deny: Iterable[str] = (),
                 allowed_extra: Iterable[str] = ()) -> Path:
    """Sérialise, RE-contrôle le texte final, puis écrit de façon atomique (fichier temporaire voisin + remplacement)."""
    dest = safe_output_path(source, out, force)
    text = json.dumps(doc_tl.to_dict(), ensure_ascii=False, indent=1)
    problems = lint_export(json.loads(text), deny, allowed_extra)
    if problems:
        raise ExportRefused("export refusé au contrôle final : " + "; ".join(problems[:10]))
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)
    return dest
