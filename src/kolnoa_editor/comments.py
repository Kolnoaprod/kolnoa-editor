"""Retours de revue rattachés au plan source, pas seulement au temps du rendu.

Un commentaire posé à 02:13.40 du rendu V2 est stocké avec le segment concerné (id stable),
le clip source et la position dans ce clip. Quand une nouvelle version est importée, les retours
encore ouverts sont reportés : ils suivent leur plan même si celui-ci a été déplacé, raccourci ou
scindé. S'il a disparu, le retour devient `orphan` (jamais perdu).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from datetime import datetime

from .decision_list import DecisionList
from .project import Project

EPS = 0.04
_CID = re.compile(r"^c(\d+)$")


@dataclass
class Comment:
    id: str
    version: int
    render_time: float | None  # secondes dans le rendu commenté (None si le plan a disparu)
    segment_id: str
    clip: str
    source_time: float         # secondes dans le clip source
    text: str
    status: str = "open"       # 'open' | 'done' | 'orphan'
    created: str = ""
    anchor: str = "exact"      # 'exact' | 'segment' (point hors plage) | 'lost'
    carried_from: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def load_comments(project: Project, version: int) -> list[Comment]:
    path = project.comments_path(version)
    if not path.is_file():
        return []
    return [Comment(**c) for c in project.read_json(path)]


def save_comments(project: Project, version: int, comments: list[Comment]) -> None:
    project.write_json(project.comments_path(version), [c.to_dict() for c in comments])


def _next_id(comments: list[Comment]) -> str:
    nums = [int(m.group(1)) for c in comments if (m := _CID.match(c.id))]
    return f"c{max(nums, default=0) + 1:03d}"


def add_comment(project: Project, dl: DecisionList, render_time: float, text: str) -> Comment:
    seg, offset = dl.locate(render_time)
    comments = load_comments(project, dl.version)
    comment = Comment(
        id=_next_id(comments),
        version=dl.version,
        render_time=round(render_time, 3),
        segment_id=seg.id,
        clip=seg.clip,
        source_time=round(seg.start + offset, 3),
        text=text.strip(),
        created=datetime.now().isoformat(timespec="seconds"),
    )
    comments.append(comment)
    save_comments(project, dl.version, comments)
    return comment


def _relocate(c: Comment, new: DecisionList) -> Comment:
    """Retrouve la position d'un retour dans la nouvelle version (même id, sinon descendants)."""
    st = c.source_time
    by_id = {s.id: s for s in new.segments}

    def inside(s) -> bool:
        return s.clip == c.clip and s.start - EPS <= st <= s.end + EPS

    def exact(s) -> Comment:
        return Comment(**{**c.to_dict(), "segment_id": s.id, "version": new.version,
                          "render_time": round(new.start_of(s.id) + (st - s.start), 3),
                          "anchor": "exact", "status": "open", "carried_from": c.version})

    same = by_id.get(c.segment_id)
    if same is not None and inside(same):
        return exact(same)
    for s in new.segments:  # un plan scindé : le retour suit la partie qui contient son point
        if new.descends_from(s.id, c.segment_id) and inside(s):
            return exact(s)
    if same is not None and same.clip == c.clip:  # plan raccourci : point exact hors plage
        return Comment(**{**c.to_dict(), "version": new.version,
                          "render_time": round(new.start_of(same.id), 3), "anchor": "segment",
                          "status": "open", "carried_from": c.version})
    return Comment(**{**c.to_dict(), "version": new.version, "render_time": None,
                      "anchor": "lost", "status": "orphan", "carried_from": c.version})


def carry_over(project: Project, old: DecisionList, new: DecisionList) -> list[Comment]:
    """Reporte les retours non traités de `old` vers `new` (sans doublon). Retourne les reportés."""
    existing = load_comments(project, new.version)
    present = {c.id for c in existing}
    carried = []
    for c in load_comments(project, old.version):
        if c.status == "done" or c.id in present:
            continue
        carried.append(_relocate(c, new))
    if carried:
        save_comments(project, new.version, existing + carried)
    return carried


def mmss(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def to_prompt(project: Project, dl: DecisionList) -> str:
    """Texte à envoyer à l'IA avec la liste de décisions : retours ouverts, par segment."""
    pending = [c for c in load_comments(project, dl.version) if c.status in ("open", "orphan")]
    if not pending:
        return "Aucun retour ouvert."
    lines = [f"Retours sur la version {dl.version} de « {dl.title} » :"]
    for c in pending:
        where = f"segment {c.segment_id} ({c.clip}, à {mmss(c.source_time)} dans le clip"
        if c.render_time is not None:
            where += f", {mmss(c.render_time)} dans le film"
        where += ")"
        note = ""
        if c.anchor == "segment":
            note = " [le point exact n'est plus dans le plan, le plan a été raccourci]"
        elif c.anchor == "lost":
            note = " [le plan commenté a été supprimé : à reprendre ailleurs si le retour reste valable]"
        lines.append(f"- [{c.id}] {where} : {c.text}{note}")
    lines.append(
        "Réponds avec une nouvelle liste de décisions (JSON, même schéma) en ne modifiant que "
        "les segments concernés ; garde les ids des segments inchangés, donne un nouvel id aux "
        "nouveaux plans, mets `parent` quand un plan est scindé, et ne réutilise jamais un id "
        "listé dans `retired`."
    )
    return "\n".join(lines)
