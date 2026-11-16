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
from .scanner import load_library, refresh, summarize
from .sync.apply import plan_audio_sync, split_discontinuous, sync_group_audio
from .sync.config import SyncConfig
from .sync.manual import align_manual, source_times_at
from .sync.metadata import metadata_hint
from .sync.sources import FileSource
from .sync.visual import VisualCandidate, accept_candidate, luma_series, propose_visual_sync
from .timeline import (AddGroup, Session, execute, Transaction, TimelineError, accept_angle, from_decision_list, lock_angle,
                       nudge_angle, op_from_dict, to_decision_list)
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
    for g in tl.groups:
        print(f"groupe multicam {g.id} « {g.name} » (maître {g.master})")
        for a in g.angles:
            lock = " 🔒" if a.locked else ""
            print(f"  {a.id}{lock} [{a.status}/{a.method}, confiance {a.confidence:.2f}] décalage {a.offset:+.3f} s, "
                  f"dérive {a.drift_ppm:+.1f} ppm — {a.media}" + (f" — {a.note}" if a.note else ""))
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


def _split_pair(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise TimelineError(f"« {spec} » : attendu ID=chemin/relatif/au/mariage")
    k, v = spec.split("=", 1)
    return k.strip(), v.strip().replace("\\", "/")


def cmd_mc_create(args) -> int:
    from .probe import probe
    project = _project(args)
    s = _session(project)
    master_id, master_path = _split_pair(args.master)
    angles = []
    for spec in [args.master] + list(args.angle or []):
        aid, rel = _split_pair(spec)
        path = project.resolve(rel)
        if not path.is_file():
            raise TimelineError(f"fichier introuvable : {rel}")
        info = probe(path)
        is_video = bool(info.vcodec)
        angles.append({"id": aid, "media": rel, "kind": "video" if is_video else "audio", "has_audio": info.has_audio,
                       **({"status": "reference", "method": "reference", "confidence": 1.0} if aid == master_id else {})})
    gid = s.tl.next_group_id()
    for line in s.do(AddGroup({"id": gid, "master": master_id, "name": args.name or "", "angles": angles})):
        print(line)
    for a in angles:
        print(f"  {a['id']}: {a['media']} ({a['kind']}, {'avec' if a['has_audio'] else 'SANS'} audio)")
    return 0


def _visual_ref(tl, g, ref_id: str | None):
    cands = [a for a in g.angles if a.kind == "video" and a.usable]
    if ref_id:
        a = g.angle(ref_id)
        if not a.usable:
            raise TimelineError(f"l'angle de référence {ref_id} n'est pas synchronisé de façon fiable")
        return a
    return cands[0] if cands else None


def cmd_mc_sync(args) -> int:
    project = _project(args)
    s = _session(project)
    cfg = SyncConfig.load(args.config)
    g = s.tl.group(args.group)
    cancel = CancelToken()
    sources = {}
    starts = {}
    try:
        lib = {f.path: f for f in load_library(project)}
    except FileNotFoundError:
        lib = {}                                   # pas d'inventaire : pas d'indice d'horodatage, rien d'autre ne change
    for a in g.angles:
        if a.has_audio:
            sources[a.id] = FileSource(project.resolve(a.media), cfg.rate, cancel, project.tmp_dir)
        if a.media in lib:
            starts[a.id] = lib[a.media].start
    hints = {}
    for a in g.angles:
        if a.id != g.master and not args.no_hint:
            h = metadata_hint(starts.get(g.master), starts.get(a.id), cfg, g.angle(g.master).offset)
            if h:
                hints[a.id] = h
    try:
        estimates = sync_group_audio(s.tl, g.id, sources, cfg, hints, cancel)
    except KeyboardInterrupt:
        print("\nAnnulé.")
        return 130
    for aid, est in estimates.items():
        print(f"{aid}: {est.status} (confiance {est.confidence:.2f}, {est.elapsed_s:.1f} s) "
              f"décalage {est.offset:+.3f} s, dérive {est.drift_ppm:+.1f} ppm"
              + ("" if est.drift_measured else " (non mesurée)"))
        for n in est.notes:
            print(f"    - {n}")
    report = plan_audio_sync(s.tl, g.id, estimates, overwrite_manual=args.overwrite_manual)
    for line in report.lines:
        print(line)
    work = s.tl.copy()                              # état « après synchro » : sert de base à la proposition visuelle
    if report.op is not None:
        execute(work, report.op)
    if args.dry_run:
        print("(simulation : rien n'a été enregistré)")
    elif report.op is not None:
        s.do(report.op, f"synchro audio {g.id}")
        print("Synchro enregistrée (annulable avec timeline-undo).")
    # drone / angles sans audio : propositions visuelles, jamais appliquées sans --accept-visual
    silent = [a for a in g.angles if not a.has_audio and a.kind == "video" and not a.locked]
    for a in silent:
        ref = _visual_ref(work, work.group(g.id), args.visual_ref)
        if ref is None:
            print(f"{a.id} (sans audio) : aucun angle vidéo synchronisé pour servir de référence visuelle "
                  "(synchroniser d'abord une caméra avec son, ou --visual-ref)")
            continue
        r = propose_visual_sync(luma_series(project.resolve(ref.media), cfg, cancel, project.tmp_dir),
                                luma_series(project.resolve(a.media), cfg, cancel, project.tmp_dir), cfg)
        print(f"{a.id} (sans audio) — synchro visuelle EXPÉRIMENTALE : {r.status} ; "
              f"{r.events_ref} événement(s) côté {ref.id}, {r.events_sig} côté {a.id}")
        for n in r.notes:
            print(f"    - {n}")
        converted = []
        for c in r.candidates:
            off_group = ref.offset + c.offset * (1.0 + ref.drift_ppm * 1e-6)
            conv = VisualCandidate(off_group, c.confidence, c.score, c.psr, c.matched_events, c.rank, c.coincidence_risk)
            converted.append(conv)
            print(f"    #{c.rank}: décalage {off_group:+.3f} s, confiance {c.confidence:.2f}, "
                  f"{c.matched_events} événement(s), risque de coïncidence {c.coincidence_risk:.3f}")
        if args.accept_visual and a.id == (args.visual_angle or a.id):
            if not 1 <= args.accept_visual <= len(converted):
                raise TimelineError(f"candidat #{args.accept_visual} inexistant")
            s.do(accept_candidate(s.tl, g.id, a.id, converted[args.accept_visual - 1]))
            print(f"Proposition #{args.accept_visual} posée sur {a.id} (état « incertain », à valider : "
                  "multicam-accept, ou régler image par image : multicam-nudge)")
    return 0


def cmd_mc_align(args) -> int:
    s = _session(_project(args))
    pts = [[float(a), float(b)] for a, b in args.point]
    for line in s.do(align_manual(s.tl, args.group, args.angle, pts, args.snap)):
        print(line)
    return 0


def cmd_mc_nudge(args) -> int:
    s = _session(_project(args))
    for line in s.do(nudge_angle(s.tl, args.group, args.angle, args.frames)):
        print(line)
    return 0


def cmd_mc_lock(args) -> int:
    s = _session(_project(args))
    for line in s.do(lock_angle(s.tl, args.group, args.angle, not args.unlock)):
        print(line)
    return 0


def cmd_mc_accept(args) -> int:
    s = _session(_project(args))
    for line in s.do(accept_angle(s.tl, args.group, args.angle)):
        print(line)
    return 0


def cmd_mc_at(args) -> int:
    s = _session(_project(args))
    for aid, t in source_times_at(s.tl, args.group, args.time).items():
        a = s.tl.group(args.group).angle(aid)
        print(f"{aid}: {'hors plage' if t is None else f'{t:.3f} s'}  ({a.media})")
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


def cmd_sync_calibrate(args) -> int:
    from .sync import calibrate as cal
    cfg = SyncConfig.load(args.config)
    root = cal.resolve_root(args.root, args.root_map)
    opts = cal.Options(root=root, out=Path(args.out), scenario=args.scenario, audio_dirs=args.audio_dir,
                       video_dirs=args.video_dir, dry_run=args.dry_run, inventory_only=args.inventory_only,
                       with_hint=args.with_hint, visual=not args.no_visual, max_pairs=args.max_pairs,
                       clock_tol_s=args.clock_tol, max_video_seconds=args.max_video_seconds, inventory_limit=args.limit,
                       only=args.only, truth=Path(args.truth) if args.truth else None, cfg=cfg, tz=args.tz)
    res = cal.run(opts)
    print(f"\nRapport : {res['out'] / 'report.md'}")
    print("Originaux inchangés : " + ("oui" if res["unchanged"] else "NON : " + ", ".join(res["changed"])))
    return 0 if res["unchanged"] else 3


def cmd_audio_measure(args) -> int:
    """Mesure de qualité sonore de fichiers (LECTURE SEULE) : sert à recaler les seuils sur de vrais rushes."""
    from dataclasses import asdict
    from .timeline import measure_audio
    rows = []
    for f in args.files:
        m = measure_audio(Path(f), args.start, args.duration)
        rows.append(asdict(m))
        print(f"{Path(f).name}: sonie {m.integrated_lufs} LUFS, crête vraie {m.true_peak_dbtp} dBTP, fond {m.noise_floor_dbfs} dBFS, "
              f"contraste {m.contrast_db} dB, saturation {m.clip_ratio:.5f}, silence {m.silence_ratio:.2f}, "
              f"{m.channels} canal/canaux {m.sample_rate} Hz")
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


def cmd_audio_propose(args) -> int:
    """Propose la meilleure source audio d'une séquence. N'APPLIQUE RIEN : `audio-master` applique, après validation humaine."""
    from .timeline import propose_master
    project = _project(args)
    prop = propose_master(_session(project).tl, args.group, args.start, args.end, project.root,
                          args.source or None)
    print("\n".join(prop.explain()))
    return 0


def cmd_audio_master(args) -> int:
    from .timeline import assign_master, clear_master
    if not args.clear and not args.source:
        raise TimelineError("indiquer la source (ou --clear pour revenir au maître du groupe)")
    s = _session(_project(args))
    op = clear_master(s.tl, args.group, args.start, args.end) if args.clear else \
        assign_master(s.tl, args.group, args.start, args.end, args.source, args.reason or "")
    for line in s.do(op):
        print(line)
    return 0


def cmd_audio_mix(args) -> int:
    from .timeline import render_mix, set_audio_settings
    project = _project(args)
    tl = _session(project).tl.copy()
    props = {k: v for k, v in (("target_lufs", args.lufs), ("ceiling_dbtp", args.ceiling)) if v is not None}
    if props:
        execute(tl, set_audio_settings(tl, **props))             # sur une copie : la timeline de la session n'est pas touchée
    out = Path(args.out)
    if out.exists() and not args.force:
        raise TimelineError(f"{out} existe déjà (--force pour remplacer)")
    rep = render_mix(tl, project.root, out)
    print(f"Mixage écrit : {out} ({rep.duration:.1f} s, {rep.events} événement(s), {rep.sample_rate} Hz)")
    fmt = lambda v: "non mesurable" if v is None else f"{v:.1f}"          # noqa: E731
    print(f"Sonie {fmt(rep.before.integrated_lufs)} → {fmt(rep.after.integrated_lufs)} LUFS ; crête vraie "
          f"{fmt(rep.before.true_peak_dbtp)} → {fmt(rep.after.true_peak_dbtp)} dBTP ; gain {rep.gain_applied_db:+.1f} dB ; "
          f"limiteur {'intervenu' if rep.limiter else 'non nécessaire'}")
    for w in rep.warnings:
        print(f"ATTENTION : {w}", file=sys.stderr)
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
    s = add("multicam-create", "Crée un groupe multicam (maître audio + angles)", cmd_mc_create)
    s.add_argument("--master", required=True, help="ID=chemin du fichier qui sert d'audio de référence")
    s.add_argument("--angle", action="append", help="ID=chemin (répétable)")
    s.add_argument("--name")
    s = add("multicam-sync", "Synchronise les angles (audio, dérive ; propositions visuelles pour le drone muet)", cmd_mc_sync)
    s.add_argument("group")
    s.add_argument("--config", help="réglages JSON (seuils de confiance, fenêtres…)")
    s.add_argument("--dry-run", action="store_true", help="calcule et affiche sans rien enregistrer")
    s.add_argument("--no-hint", action="store_true", help="ignore l'indice des horodatages de fichiers")
    s.add_argument("--overwrite-manual", action="store_true", help="autorise à écraser un réglage manuel")
    s.add_argument("--visual-ref", help="angle vidéo de référence pour la synchro visuelle")
    s.add_argument("--accept-visual", type=int, help="pose la proposition visuelle n° N (état « incertain »)")
    s.add_argument("--visual-angle", help="limite --accept-visual à cet angle")
    s = add("multicam-align", "Alignement manuel par repères (1 repère = décalage, 2 = dérive)", cmd_mc_align)
    s.add_argument("group")
    s.add_argument("angle")
    s.add_argument("--point", nargs=2, type=float, action="append", required=True, metavar=("T_ANGLE", "T_GROUPE"))
    s.add_argument("--snap", action="store_true", help="arrondit le décalage à l'image")
    s = add("multicam-nudge", "Décale un angle image par image (+/-)", cmd_mc_nudge)
    s.add_argument("group")
    s.add_argument("angle")
    s.add_argument("frames", type=int)
    s = add("multicam-lock", "Verrouille (ou déverrouille) la synchro d'un angle", cmd_mc_lock)
    s.add_argument("group")
    s.add_argument("angle")
    s.add_argument("--unlock", action="store_true")
    s = add("multicam-accept", "Valide humainement une synchro incertaine ou visuelle", cmd_mc_accept)
    s.add_argument("group")
    s.add_argument("angle")
    s = add("multicam-at", "Instants source de chaque angle à un instant de groupe (pour l'alignement côte à côte)", cmd_mc_at)
    s.add_argument("group")
    s.add_argument("time", type=float)
    add("timeline-undo", "Annule la dernière action", cmd_tl_undo)
    add("timeline-redo", "Rétablit la dernière action annulée", cmd_tl_redo)
    s = add("timeline-export", "Exporte la timeline en liste de décisions", cmd_tl_export)
    s.add_argument("out")
    s.add_argument("--force", action="store_true")
    s = add("timeline-render", "Rend la timeline courante en MP4", cmd_tl_render)
    s.add_argument("--final", action="store_true")

    s = sub.add_parser("sync-calibrate", help="Mesure la synchro sur de vrais rushes (LECTURE SEULE, rapport dans --out)")
    s.add_argument("root", help="dossier du mariage, chemin Windows accepté (G:\\Mon Drive\\KOLNOA\\MARIAGE …)")
    s.add_argument("--out", required=True, help="dossier du rapport (jamais dans les rushes)")
    s.add_argument("--scenario", default="mairie", help="mairie | jeremy | clip | custom (avec --audio-dir/--video-dir)")
    s.add_argument("--audio-dir", action="append", default=[], help="dossier audio relatif (le premier = enregistreur de référence)")
    s.add_argument("--video-dir", action="append", default=[], help="dossier vidéo relatif (le premier = caméra de référence)")
    s.add_argument("--root-map", action="append", default=[], metavar="CHEMIN_WINDOWS=CHEMIN_LOCAL",
                   help="correspondance quand le dossier est monté ailleurs (non Windows)")
    s.add_argument("--dry-run", action="store_true", help="inventaire + plan seulement : aucun média n'est décodé")
    s.add_argument("--inventory-only", action="store_true", help="inventaire (avec test de silence audio) sans synchro")
    s.add_argument("--no-visual", action="store_true")
    s.add_argument("--with-hint", action="store_true", help="relance aussi chaque paire audio avec l'indice des horodatages")
    s.add_argument("--max-pairs", type=int, default=12)
    s.add_argument("--clock-tol", type=float, default=7200.0, help="écart d'horloge toléré entre appareils pour choisir les paires (s)")
    s.add_argument("--max-video-seconds", type=float, default=300.0, help="durée analysée de chaque vidéo (synchro visuelle)")
    s.add_argument("--only", action="append", default=[], help="ne garder que les fichiers dont le chemin contient ce texte")
    s.add_argument("--limit", type=int, help="nombre maximal de fichiers inventoriés par dossier")
    s.add_argument("--config", help="réglages de synchro JSON")
    s.add_argument("--truth", help="JSON de repères posés à la main : [{ref, sig, t_ref, t_sig}]")
    s.add_argument("--tz", default="Europe/Paris", help="fuseau des noms de fichiers horodatés")
    s.set_defaults(func=cmd_sync_calibrate)

    s = sub.add_parser("audio-measure", help="Mesure la qualité sonore de fichiers (LECTURE SEULE)")
    s.add_argument("files", nargs="+")
    s.add_argument("--start", type=float, default=0.0)
    s.add_argument("--duration", type=float)
    s.add_argument("--out", help="écrit les mesures en JSON")
    s.set_defaults(func=cmd_audio_measure)
    s = add("audio-propose", "Propose la meilleure source audio d'une séquence (n'applique rien)", cmd_audio_propose)
    s.add_argument("group"); s.add_argument("start", type=float); s.add_argument("end", type=float)
    s.add_argument("--source", action="append", help="limiter aux angles donnés")
    s = add("audio-master", "Choisit (ou efface) la source audio d'une séquence", cmd_audio_master)
    s.add_argument("group"); s.add_argument("start", type=float); s.add_argument("end", type=float)
    s.add_argument("source", nargs="?"); s.add_argument("--reason"); s.add_argument("--clear", action="store_true")
    s = add("audio-mix", "Rend le mixage audio de la timeline en WAV (sonie/limiteur mesurés)", cmd_audio_mix)
    s.add_argument("out"); s.add_argument("--lufs", type=float, help="cible de sonie intégrée"); s.add_argument("--ceiling", type=float)
    s.add_argument("--force", action="store_true")

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
