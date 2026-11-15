"""Versions d'un montage : import d'une liste produite par l'IA (ou à la main), avec reprise des retours."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .comments import carry_over, Comment
from .decision_list import DecisionList, DecisionListError
from .project import Project

log = logging.getLogger("kolnoa.versions")


@dataclass
class ImportResult:
    dl: DecisionList
    path: Path
    previous: DecisionList | None
    carried: list[Comment]
    warnings: list[str]


def load_version(project: Project, version: int) -> DecisionList:
    path = project.version_path(version)
    if not path.is_file():
        raise DecisionListError(f"Version {version} introuvable : {path}")
    return DecisionList.load(path)


def import_version(project: Project, file: Path) -> ImportResult:
    """Valide `file`, l'enregistre comme version suivante et reporte les retours ouverts.

    Le numéro de version est attribué par Kolnoa (celui du fichier est ignoré), pour qu'une
    erreur de l'IA ne puisse jamais écraser une version existante.
    """
    project.ensure()
    new = DecisionList.load(file)
    new.validate(project)
    previous_no = project.latest_version()
    previous = load_version(project, previous_no) if previous_no else None
    warnings: list[str] = []
    if previous is not None:
        reused = sorted({s.id for s in new.segments} & set(previous.retired))
        if reused:
            raise DecisionListError(
                f"id(s) supprimé(s) dans une version précédente et réutilisé(s) : {', '.join(reused)}"
            )
        old_by = {s.id: s for s in previous.segments}
        for seg in new.segments:
            old = old_by.get(seg.id)
            if old is not None and old.clip != seg.clip:
                warnings.append(
                    f"{seg.id} change de clip ({old.clip} → {seg.clip}) : ses retours ne seront pas "
                    "reportés tels quels ; un nouveau plan devrait avoir un nouvel id."
                )
        for rid in previous.retired:
            if rid not in new.retired:
                new.retired.append(rid)
        for sid in (s.id for s in previous.segments):
            if sid not in {s.id for s in new.segments} and sid not in new.retired:
                new.retired.append(sid)
    new.version = project.next_version()
    path = project.version_path(new.version)
    new.save(path)
    carried = carry_over(project, previous, new) if previous is not None else []
    log.info("version %d importée (%d retours reportés)", new.version, len(carried))
    return ImportResult(new, path, previous, carried, warnings)
