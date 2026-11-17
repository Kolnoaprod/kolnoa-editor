"""Anonymisation d'une timeline pour en faire un jeu de test public (dépôt public : aucun nom de client, de lieu ni de fichier).

Remplace chemins de médias, titre, noms de groupes, identifiants d'angles, étiquettes, notes, chutier et musique par des
libellés génériques, de façon COHÉRENTE (un même média garde le même faux nom partout). Les positions, durées, pistes,
groupes, fondus et repères sont conservés : les statistiques d'analyse restent identiques. Le texte des repères `chapter`
est CONSERVÉ (c'est le type de séquence qu'on veut apprendre) mais renvoyé dans le rapport pour relecture humaine."""

from __future__ import annotations

from typing import Any

from ...timeline import Timeline


def anonymize_timeline(tl: Timeline) -> tuple[Timeline, dict[str, Any]]:
    out = Timeline.from_dict(tl.to_dict())          # copie : l'original n'est jamais modifié
    media_map: dict[str, str] = {}

    def media(path: str) -> str:
        return media_map.setdefault(path, f"rush-{len(media_map) + 1:03d}.mp4")

    out.title = "Projet anonymisé"
    if out.music:
        out.music = {**out.music, "path": "musique.wav"}
    for g_i, g in enumerate(out.groups, 1):
        amap = {a.id: f"angle{n}" for n, a in enumerate(g.angles, 1)}
        g.name = f"groupe {g_i}"
        g.master = amap.get(g.master, g.master)
        for a in g.angles:
            a.id, a.media, a.note = amap[a.id], media(a.media), ""
        for c in out.clips:
            if c.group == g.id and c.angle in amap:
                c.angle = amap[c.angle]
        for r in out.masters:
            if r.group == g.id:
                r.source, r.reason = amap.get(r.source, r.source), ""
    for c in out.clips:
        c.media, c.label, c.note, c.extra = media(c.media), "", "", {}
    for s in out.bin:
        s.media, s.name, s.tags, s.note = media(s.media), "", [], ""
    for m in out.markers:
        if m.kind != "chapter":
            m.text = ""
    out.validate()
    chapters = sorted({m.text for m in out.markers if m.kind == "chapter"})
    return out, {"n_media": len(media_map), "chapters_kept_for_review": chapters}
