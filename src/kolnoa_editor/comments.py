"""Retours de revue rattachés au plan source, pas seulement au temps du rendu.

Un commentaire posé à 02:13.40 du rendu V2 est stocké avec le segment concerné (id stable),
le clip source et la position dans ce clip. Après la V3, il reste exploitable.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime

from .decision_list import DecisionList
from .project import Project


@dataclass
class Comment:
    id: str
    version: int
    render_time: float        # secondes dans le rendu commenté
    segment_id: str
    clip: str
    source_time: float        # secondes dans le clip source
    text: str
    status: str = "open"      # 'open' | 'done'
    created: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def load_comments(project: Project, version: int) -> list[Comment]:
    path = project.comments_path(version)
    if not path.is_file():
        return []
    return [Comment(**c) for c in project.read_json(path)]


def save_comments(project: Project, version: int, comments: list[Comment]) -> None:
    project.write_json(project.comments_path(version), [c.to_dict() for c in comments])


def add_comment(project: Project, dl: DecisionList, render_time: float, text: str) -> Comment:
    seg, offset = dl.locate(render_time)
    comments = load_comments(project, dl.version)
    comment = Comment(
        id=f"c{len(comments) + 1:03d}",
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


def mmss(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def to_prompt(project: Project, dl: DecisionList) -> str:
    """Texte à envoyer à l'IA avec la liste de décisions : retours ouverts, par segment."""
    open_comments = [c for c in load_comments(project, dl.version) if c.status == "open"]
    if not open_comments:
        return "Aucun retour ouvert."
    lines = [f"Retours sur la version {dl.version} de « {dl.title} » :"]
    for c in open_comments:
        lines.append(
            f"- [{c.id}] segment {c.segment_id} ({c.clip}, à {mmss(c.source_time)} dans le clip, "
            f"{mmss(c.render_time)} dans le film) : {c.text}"
        )
    lines.append(
        "Réponds avec une nouvelle liste de décisions (JSON, même schéma) en ne modifiant que "
        "les segments concernés ; garde les ids des segments inchangés."
    )
    return "\n".join(lines)
