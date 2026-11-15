"""Ligne de commande : `kolnoa <commande> <dossier mariage> ...`"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .comments import add_comment, to_prompt
from .decision_list import DecisionList, DecisionListError, diff
from .ffmpeg import CancelToken, Cancelled, FFmpegError
from .preflight import PreflightError, format_issues, has_errors, preflight
from .project import Project
from .proxies import build_proxies
from .renderer import RenderError, clean_cache, render
from .scanner import refresh, summarize
from .timeline import (Session, Transaction, TimelineError, from_decision_list, op_from_dict,
                       to_decision_list)
from .versions import import_version, load_version


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} Go"


def _progress(i: int, n: int, label: str) -> None:
    print(f"  [{i}/{n}] {label}", flush=True)


def _project(args) -> Project:
    project = Project(args.root, args.data_dir)
    project.ensure()
    project.configure_logging()
    return project


def cmd_refresh(args) -> int:
    project = _project(args)
    files, report = refresh(
        project,
        args.subdir,
        probe_media=getattr(args, "probe", False),
        with_hash=getattr(args, "hash", False),
        prune=getattr(args, "prune", False),
    )
    for key, v in sorted(summarize(files).items()):
        print(f"{key:45s} {v['files']:5d} fichiers  {_gb(v['bytes'])}")
    print(f"\n{len(files)} fichiers ({report.summary()}) -> {project.library_path}")
    for rel in report.missing:
        print(f"  manquant : {rel}")
    for rel in report.changed:
        print(f"  modifié  : {rel}")
    return 0


def cmd_proxies(args) -> int:
    project = _project(args)
    files, report = refresh(project)  # toujours à jour : jamais de bibliothèque périmée
    if args.subdir:
        prefix = args.subdir.strip("/\\").replace("\\", "/") + "/"
        files = [f for f in files if f.rel.startswith(prefix)]
    cancel = CancelToken()
    try:
        result = build_proxies(project, files, args.height, _progress, cancel)
    except KeyboardInterrupt:
        print("\nInterrompu. Relance la commande : elle reprend où elle s'est arrêtée.")
        return 130
    print(f"Proxys : {len(result.created)} créés, {len(result.skipped)} déjà à jour, "
          f"{len(result.failed)} en échec")
    for rel, msg in result.failed:
        print(f"  échec : {rel} — {msg}", file=sys.stderr)
    return 1 if result.failed else 0


def cmd_check(args) -> int:
    project = _project(args)
    dl = DecisionList.load(Path(args.file))
    issues = preflight(project, dl, "final" if args.final else "proxy")
    print(format_issues(issues))
    return 2 if has_errors(issues) else 0


def cmd_validate(args) -> int:
    project = _project(args)
    dl = DecisionList.load(Path(args.file))
    dl.validate(project)
    print(f"OK : « {dl.title} » v{dl.version}, {len(dl.segments)} segments, {dl.duration:.1f}s")
    return 0


def cmd_render(args) -> int:
    project = _project(args)
    dl = DecisionList.load(Path(args.file))
    cancel = CancelToken()
    try:
        out = render(project, dl, "final" if args.final else "proxy", on_progress=_progress, cancel=cancel)
    except KeyboardInterrupt:
        print("\nAnnulé. Les segments déjà encodés sont gardés : relance pour reprendre.")
        return 130
    except Cancelled:
        print("Annulé.")
        return 130
    print(f"Rendu : {out}")
    return 0


def cmd_comment(args) -> int:
    project = _project(args)
    dl = DecisionList.load(Path(args.file))
    c = add_comment(project, dl, args.time, args.text)
    print(f"{c.id} -> segment {c.segment_id} ({c.clip} à {c.source_time:.2f}s)")
    return 0


def cmd_feedback(args) -> int:
    project = _project(args)
    dl = DecisionList.load(Path(args.file))
    print(to_prompt(project, dl))
    return 0


def cmd_import(args) -> int:
    project = _project(args)
    result = import_version(project, Path(args.file))
    print(f"Version {result.dl.version} enregistrée : {result.path}")
    if result.previous is not None:
        lines = diff(result.previous, result.dl)
        print("\n".join(lines) if lines else "Aucune différence avec la version précédente.")
    print(f"{len(result.carried)} retour(s) reporté(s)")
    for w in result.warnings:
        print(f"ATTENTION : {w}", file=sys.stderr)
    return 0


def _session(project: Project) -> Session:
    return Session.open(project.timeline_dir)


def cmd_tl_import(args) -> int:
    project = _project(args)
    dl = DecisionList.load(Path(args.file))
    dl.validate(project)
    if (project.timeline_dir / "base.json").exists():
        if not args.force:
            raise TimelineError("Une timeline existe déjà (--force la met de côté et en crée une nouvelle)")
        backup = project.quarantine_dir(project.timeline_dir)
        print(f"Ancienne timeline conservée : {backup}")
    s = Session.create(project.timeline_dir, from_decision_list(dl))
    print(f"Timeline créée : {len(s.tl.clips)} plan(s), {s.tl.end_frame} images ({s.tl.duration:.1f} s)")
    return 0


def cmd_tl_show(args) -> int:
    s = _session(_project(args))
    tl = s.tl
    print(f"« {tl.title} » v{tl.version} — {tl.fps} i/s, {tl.resolution[0]}x{tl.resolution[1]}, "
          f"{tl.end_frame} images ({tl.duration:.1f} s)")
    for t in tl.tracks:
        flags = " [verrouillée]" if t.locked else ""
        print(f"{t.id} ({t.kind}, {t.role}){flags}")
        for c in tl.clips_on(t.id):
            lock = " 🔒" if c.locked else ""
            print(f"  {c.id}{lock} {c.start:>6}-{c.end:<6} {c.media} @ {c.src_in:.2f}s"
                  + (f"  — {c.note}" if c.note else ""))
    for m in tl.markers:
        print(f"repère {m.id} ({m.kind}) image {m.frame} {m.text}")
    print(f"Historique : {len(s.history())} action(s) annulable(s)")
    return 0


def cmd_tl_apply(args) -> int:
    """Applique un fichier JSON d'opérations (liste) comme UNE action annulable."""
    project = _project(args)
    s = _session(project)
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    ops = [op_from_dict(o) for o in (data["ops"] if isinstance(data, dict) else data)]
    label = data.get("label", "") if isinstance(data, dict) else ""
    for line in s.do(Transaction(ops, label), label):
        print(line)
    return 0


def cmd_tl_undo(args) -> int:
    print(f"Annulé : {_session(_project(args)).undo()}")
    return 0


def cmd_tl_redo(args) -> int:
    print(f"Rétabli : {_session(_project(args)).redo()}")
    return 0


def cmd_tl_export(args) -> int:
    project = _project(args)
    dl, warnings = to_decision_list(_session(project).tl)
    dl.validate(project)
    out = Path(args.out)
    if out.exists() and not args.force:
        raise TimelineError(f"{out} existe déjà (--force pour remplacer)")
    dl.save(out)
    print(f"Liste de décisions écrite : {out}")
    for w in warnings:
        print(f"ATTENTION : {w}", file=sys.stderr)
    return 0


def cmd_tl_render(args) -> int:
    project = _project(args)
    dl, warnings = to_decision_list(_session(project).tl)
    for w in warnings:
        print(f"ATTENTION : {w}", file=sys.stderr)
    try:
        out = render(project, dl, "final" if args.final else "proxy", on_progress=_progress, cancel=CancelToken())
    except (KeyboardInterrupt, Cancelled):
        print("\nAnnulé. Les segments déjà encodés sont gardés : relance pour reprendre.")
        return 130
    print(f"Rendu : {out}")
    return 0


def cmd_diff(args) -> int:
    project = _project(args)
    a, b = load_version(project, args.old), load_version(project, args.new)
    lines = diff(a, b)
    print("\n".join(lines) if lines else "Aucune différence.")
    return 0


def cmd_clean_cache(args) -> int:
    project = _project(args)
    print(f"Cache de segments vidé : {clean_cache(project)} Mo libérés (rushes et rendus intacts)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kolnoa", description="Kolnoa Editor")
    p.add_argument("--version", action="version", version=__version__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", help="dossier des données Kolnoa (proxys, cache, rendus) ; "
                                           "défaut <mariage>/.kolnoa ou variable KOLNOA_DATA_DIR")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_, func):
        s = sub.add_parser(name, help=help_, parents=[common])
        s.add_argument("root", help="dossier du mariage")
        s.set_defaults(func=func)
        return s

    s = add("scan", "Inventaire des rushes (incrémental : ne perd rien)", cmd_refresh)
    s.add_argument("--subdir")
    s.add_argument("--probe", action="store_true", help="lit durée et audio des nouveaux fichiers")

    s = add("refresh", "Met la bibliothèque à jour (nouveaux, modifiés, manquants)", cmd_refresh)
    s.add_argument("--subdir")
    s.add_argument("--probe", action="store_true")
    s.add_argument("--hash", action="store_true", help="empreinte de contenu rapide (taille + échantillons)")
    s.add_argument("--prune", action="store_true", help="retire de la bibliothèque les fichiers disparus")

    s = add("proxies", "Crée ou reprend les proxys 540p", cmd_proxies)
    s.add_argument("--subdir")
    s.add_argument("--height", type=int, default=540)

    s = add("check", "Vérifie une liste de décisions avant rendu", cmd_check)
    s.add_argument("file")
    s.add_argument("--final", action="store_true")

    s = add("validate", "Valide une liste de décisions (structure et fichiers)", cmd_validate)
    s.add_argument("file")

    s = add("render", "Rend une liste de décisions en MP4 (reprend après interruption)", cmd_render)
    s.add_argument("file")
    s.add_argument("--final", action="store_true", help="depuis les originaux (sinon proxys)")

    s = add("comment", "Ajoute un retour à un instant du rendu", cmd_comment)
    s.add_argument("file")
    s.add_argument("time", type=float, help="secondes dans le rendu")
    s.add_argument("text")

    s = add("feedback", "Texte des retours ouverts à envoyer à l'IA", cmd_feedback)
    s.add_argument("file")

    s = add("import-version", "Enregistre une liste de l'IA comme nouvelle version et reporte les retours", cmd_import)
    s.add_argument("file")

    s = add("diff", "Compare deux versions enregistrées", cmd_diff)
    s.add_argument("old", type=int)
    s.add_argument("new", type=int)

    s = add("timeline-import", "Crée la timeline éditable depuis une liste de décisions", cmd_tl_import)
    s.add_argument("file")
    s.add_argument("--force", action="store_true", help="met l'ancienne timeline de côté (jamais supprimée)")
    add("timeline-show", "Affiche la timeline courante", cmd_tl_show)
    s = add("timeline-apply", "Applique un fichier JSON d'opérations (une action annulable)", cmd_tl_apply)
    s.add_argument("file")
    add("timeline-undo", "Annule la dernière action", cmd_tl_undo)
    add("timeline-redo", "Rétablit la dernière action annulée", cmd_tl_redo)
    s = add("timeline-export", "Exporte la timeline en liste de décisions", cmd_tl_export)
    s.add_argument("out")
    s.add_argument("--force", action="store_true")
    s = add("timeline-render", "Rend la timeline courante en MP4", cmd_tl_render)
    s.add_argument("--final", action="store_true")

    add("clean-cache", "Vide le cache de segments (jamais les rushes ni les rendus)", cmd_clean_cache)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PreflightError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    except (DecisionListError, TimelineError, FileNotFoundError, ValueError, RenderError, FFmpegError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
