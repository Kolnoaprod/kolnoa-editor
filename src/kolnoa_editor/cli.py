"""Ligne de commande : `kolnoa <commande> <dossier mariage> ...`"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .comments import add_comment, to_prompt
from .decision_list import DecisionList, DecisionListError
from .project import Project
from .proxies import build_proxies
from .renderer import render
from .scanner import load_library, save_library, scan, summarize


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} Go"


def _progress(i: int, n: int, label: str) -> None:
    print(f"  [{i}/{n}] {label}", flush=True)


def cmd_scan(args) -> int:
    project = Project(args.root)
    files = scan(project, args.subdir)
    if args.probe:
        from .probe import probe

        for i, f in enumerate(files, 1):
            _progress(i, len(files), f.rel)
            info = probe(project.resolve(f.rel))
            f.duration, f.has_audio = info.duration, info.has_audio
    path = save_library(project, files)
    for key, v in sorted(summarize(files).items()):
        print(f"{key:45s} {v['files']:5d} fichiers  {_gb(v['bytes'])}")
    print(f"\n{len(files)} fichiers -> {path}")
    return 0


def cmd_proxies(args) -> int:
    project = Project(args.root)
    files = load_library(project) if project.library_path.is_file() else scan(project)
    if args.subdir:
        files = [f for f in files if f.rel.startswith(args.subdir.strip("/") + "/")]
    created, skipped = build_proxies(project, files, args.height, _progress)
    print(f"Proxys : {created} créés, {skipped} déjà à jour")
    return 0


def cmd_validate(args) -> int:
    project = Project(args.root)
    dl = DecisionList.load(Path(args.file))
    dl.validate(project)
    print(f"OK : « {dl.title} » v{dl.version}, {len(dl.segments)} segments, {dl.duration:.1f}s")
    return 0


def cmd_render(args) -> int:
    project = Project(args.root)
    dl = DecisionList.load(Path(args.file))
    out = render(project, dl, "final" if args.final else "proxy", on_progress=_progress)
    print(f"Rendu : {out}")
    return 0


def cmd_comment(args) -> int:
    project = Project(args.root)
    dl = DecisionList.load(Path(args.file))
    c = add_comment(project, dl, args.time, args.text)
    print(f"{c.id} -> segment {c.segment_id} ({c.clip} à {c.source_time:.2f}s)")
    return 0


def cmd_feedback(args) -> int:
    project = Project(args.root)
    dl = DecisionList.load(Path(args.file))
    print(to_prompt(project, dl))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kolnoa", description="Kolnoa Editor")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Inventaire des rushes du dossier mariage")
    s.add_argument("root")
    s.add_argument("--subdir")
    s.add_argument("--probe", action="store_true", help="lit durée et audio (plus long)")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("proxies", help="Crée les proxys 540p")
    s.add_argument("root")
    s.add_argument("--subdir")
    s.add_argument("--height", type=int, default=540)
    s.set_defaults(func=cmd_proxies)

    s = sub.add_parser("validate", help="Valide une liste de décisions")
    s.add_argument("root")
    s.add_argument("file")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("render", help="Rend une liste de décisions en MP4")
    s.add_argument("root")
    s.add_argument("file")
    s.add_argument("--final", action="store_true", help="depuis les originaux (sinon proxys)")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("comment", help="Ajoute un retour à un instant du rendu")
    s.add_argument("root")
    s.add_argument("file")
    s.add_argument("time", type=float, help="secondes dans le rendu")
    s.add_argument("text")
    s.set_defaults(func=cmd_comment)

    s = sub.add_parser("feedback", help="Texte des retours ouverts à envoyer à l'IA")
    s.add_argument("root")
    s.add_argument("file")
    s.set_defaults(func=cmd_feedback)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (DecisionListError, FileNotFoundError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
