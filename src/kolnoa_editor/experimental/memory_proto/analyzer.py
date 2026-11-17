"""Analyse d'un projet terminé : statistiques de montage par grande séquence (PROTOTYPE, lecture seule).

Ce module ne modifie jamais la timeline (il travaille sur une lecture) et ne produit AUCUN chemin de média, nom de fichier ni
image : seulement des nombres et des libellés de sections. Il ne mesure que ce que la timeline contient réellement ;
ce qu'il ne peut pas mesurer (taille de plan, ralentis, rapport à la musique, transitions vidéo) est listé dans `unmeasured`
au lieu d'être deviné.

Définitions (volontairement structurelles et explicites) :
  - sections : repères de nature `chapter` posés par l'humain, `text` = type de séquence ; une section va jusqu'au repère suivant.
  - plans : plans des pistes vidéo de rôle `main` ; un plan appartient à la section où il COMMENCE.
  - changement d'angle : deux plans contigus du même groupe multicam avec des angles différents.
  - plan de coupe : plan d'une piste vidéo dont le rôle n'est pas `main`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ...timeline import Timeline

SCHEMA = "kolnoa.memory.profile_stats/0"
ANALYZER_VERSION = "0-prototype"
UNSECTIONED = "(sans chapitre)"
UNMEASURED = ["taille de plan", "raccords de regard/mouvement", "ralentis", "relation aux coupes et à la musique",
              "transitions vidéo", "valeur émotionnelle"]


def _quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    v = sorted(values)

    def q(p: float) -> float:
        k = (len(v) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(v) - 1)
        return v[lo] + (v[hi] - v[lo]) * (k - lo)

    return {"min": round(v[0], 3), "p25": round(q(0.25), 3), "median": round(q(0.5), 3), "p75": round(q(0.75), 3),
            "p90": round(q(0.9), 3), "max": round(v[-1], 3), "mean": round(sum(v) / len(v), 3)}


def _confidence(n: int) -> str:
    return "faible" if n < 10 else "moyenne" if n < 30 else "bonne"


def _fingerprint(tl: Timeline) -> str:
    return hashlib.sha1(json.dumps(tl.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def analyze_timeline(tl: Timeline, hold_s: float = 6.0) -> dict[str, Any]:
    """Statistiques de montage de `tl` (non modifiée). `hold_s` : seuil explicite d'un plan « qui respire »."""
    fps = tl.fps
    warnings: list[str] = []
    main_ids = {t.id for t in tl.tracks if t.kind == "video" and t.role == "main"}
    side_ids = {t.id for t in tl.tracks if t.kind == "video" and t.role != "main"}
    shots = sorted((c for c in tl.clips if c.track in main_ids), key=lambda c: (c.start, c.id))
    cuts = sorted((c for c in tl.clips if c.track in side_ids), key=lambda c: (c.start, c.id))
    end = max([c.end for c in tl.clips], default=0)

    chapters = sorted((m for m in tl.markers if m.kind == "chapter"), key=lambda m: m.frame)
    if chapters:
        bounds = [(m.text.strip() or "(sans nom)", m.frame, chapters[i + 1].frame if i + 1 < len(chapters) else end)
                  for i, m in enumerate(chapters)]
        if chapters[0].frame > 0 and any(c.start < chapters[0].frame for c in tl.clips):
            warnings.append("des plans commencent avant le premier chapitre : ils sont comptés dans « (avant le premier chapitre) »")
            bounds.insert(0, ("(avant le premier chapitre)", 0, chapters[0].frame))
    else:
        warnings.append("aucun repère de chapitre : une seule section ; poser des repères `chapter` pour obtenir une structure")
        bounds = [(UNSECTIONED, 0, end)]
    if not shots:
        warnings.append("aucun plan sur une piste vidéo de rôle `main` : rien à mesurer")

    xf_by_left = {x.left: x for x in tl.crossfades}
    sections = []
    for kind, s0, s1 in bounds:
        sh = [c for c in shots if s0 <= c.start < s1]
        cu = [c for c in cuts if s0 <= c.start < s1]
        dur_s = (s1 - s0) / fps
        minutes = dur_s / 60 if dur_s > 0 else None
        durations = [c.duration / fps for c in sh]
        switches = sum(1 for a, b in zip(sh, sh[1:])
                       if a.group and a.group == b.group and a.angle != b.angle and a.end == b.start)
        total = sum(durations)
        held = sum(d for d in durations if d >= hold_s)
        xfs = [xf_by_left[c.id].duration for c in sh if c.id in xf_by_left]
        sections.append({
            "kind": kind, "start": s0, "end": s1, "duration_s": round(dur_s, 3),
            "shots": {"n": len(sh), "durations_s": _quantiles(durations)},
            "cuts_per_min": round(len(sh) / minutes, 3) if minutes else None,
            "angle_switches": switches,
            "angle_switches_per_min": round(switches / minutes, 3) if minutes else None,
            "hold_ratio": round(held / total, 4) if total else None,
            "cutaways": {"n": len(cu), "per_min": round(len(cu) / minutes, 3) if minutes else None,
                         "durations_s": _quantiles([c.duration / fps for c in cu])},
            "crossfades_audio": {"n": len(xfs), "durations_s": _quantiles(xfs)},
            "confidence": _confidence(len(sh)),
        })
    all_d = [c.duration / fps for c in shots]
    return {"schema": SCHEMA, "analyzer_version": ANALYZER_VERSION, "fps": fps, "hold_s": hold_s,
            "fingerprint": _fingerprint(tl), "sections": sections,
            "global": {"n_shots": len(shots), "n_cutaways": len(cuts), "duration_s": round(end / fps, 3),
                       "shot_durations_s": _quantiles(all_d)},
            "angle_roles": None,      # absent tant que le réalisateur n'a pas affecté de rôles : jamais deviné
            "warnings": warnings, "unmeasured": list(UNMEASURED)}


def render_report(stats: dict[str, Any]) -> str:
    """Rapport lisible (Markdown). Ne contient aucun nom de média."""
    g = stats["global"]
    out = [f"# Profil de réalisation — PROTOTYPE ({stats['analyzer_version']})", "",
           f"{g['n_shots']} plans, {g['n_cutaways']} plans de coupe, {g['duration_s']} s ; empreinte {stats['fingerprint']}.", "",
           "| Section | Durée (s) | Plans | Médiane (s) | Coupes/min | Chgts d'angle/min | Respire | Coupe | Confiance |",
           "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for s in stats["sections"]:
        d = s["shots"]["durations_s"]
        out.append(f"| {s['kind']} | {s['duration_s']} | {s['shots']['n']} | {d['median'] if d else '—'} | "
                   f"{s['cuts_per_min'] if s['cuts_per_min'] is not None else '—'} | "
                   f"{s['angle_switches_per_min'] if s['angle_switches_per_min'] is not None else '—'} | "
                   f"{s['hold_ratio'] if s['hold_ratio'] is not None else '—'} | {s['cutaways']['n']} | {s['confidence']} |")
    if stats["warnings"]:
        out += ["", "Avertissements :"] + [f"- {w}" for w in stats["warnings"]]
    out += ["", "Non mesuré (jamais deviné) : " + ", ".join(stats["unmeasured"]) + "."]
    return "\n".join(out) + "\n"
