"""Contrôleur de projet SANS interface : tout le workflow de bout en bout, utilisable par la fenêtre Qt, la ligne de commande et les tests.

    ouvrir/créer → importer les rushes → « Monte-moi cette séquence » → consigne ciblée / retouche manuelle → enregistrer → rouvrir

Il n'y a QU'UNE timeline et UNE Session : le monteur automatique, les consignes et la souris passent par les mêmes transactions."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..auto import (DirectiveResult, EditContext, RushInfo, StyleParams, analyze_silences, apply_directive, generate_first_edit,
                    parse_directive, plan_sequence, speech_moments)
from ..auto.directives import DirectiveError
from ..auto.visual import VisualParams
from ..project import Project
from ..scanner import SourceFile, load_library, refresh, save_library
from ..timeline import Scope, Selection, Session, Timeline
from ..timeline.model import Track

Analyzer = Callable[[RushInfo, Project], list]


def default_analyzer(r: RushInfo, project: Project) -> list:
    """Analyse locale par ffmpeg (parole/silence). Un rush sans piste son n'a pas de moment : le planificateur lui donne un plan d'ambiance."""
    if not r.has_audio:
        return []
    return speech_moments(r.duration, analyze_silences(project.root / r.media, r.duration))


class ProjectController:
    def __init__(self, project: Project, session: Session):
        self.project, self.session = project, session
        self.rushes: dict[str, RushInfo] = {}
        self._load_rushes(load_library(project) if project.library_path.exists() else [])

    # --- ouverture ----------------------------------------------------------
    @classmethod
    def open(cls, root: Path | str, data_dir: Path | str | None = None, title: str | None = None) -> "ProjectController":
        project = Project(root, data_dir)
        d = project.timeline_dir
        if (d / "base.json").exists():
            session = Session.open(d)
        else:
            tl = Timeline(title=title or project.root.name, tracks=[Track("V1", "video", "main"), Track("A1", "audio", "main")])
            session = Session.create(d, tl)
        return cls(project, session)

    @property
    def tl(self) -> Timeline:
        return self.session.tl

    # --- rushes ---------------------------------------------------------------
    def _load_rushes(self, files: list[SourceFile]) -> None:
        for f in files:
            if f.missing or f.kind != "video" or not f.duration:
                continue
            self.rushes.setdefault(f.rel, RushInfo(f.rel, f.duration, f.start, f.group, f.device, bool(f.has_audio)))

    def import_rushes(self, subdir: str | None = None, probe_media: bool = True) -> int:
        """Scanne le dossier (lecture seule), met à jour la bibliothèque, retourne le nombre de rushes vidéo utilisables."""
        files, _ = refresh(self.project, subdir, probe_media=probe_media)
        save_library(self.project, files)
        self._load_rushes(files)
        if not self.tl.clips and probe_media:
            self.apply_format()                      # projet vide : il prend la cadence et la résolution de ses rushes
        return len(self.rushes)

    def add_rushes(self, infos: list[RushInfo]) -> None:
        """Injection directe (tests, import depuis une autre source)."""
        for r in infos:
            self.rushes[r.media] = r

    def sequences(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rushes.values():
            out[r.group] = out.get(r.group, 0) + 1
        return dict(sorted(out.items()))

    def analyze(self, analyzer: Analyzer | None = None, groups: list[str] | None = None) -> None:
        an = analyzer or default_analyzer
        for r in self.rushes.values():
            if (groups is None or r.group in groups) and not r.moments:
                r.moments = an(r, self.project)

    # --- montage automatique ----------------------------------------------------
    def build(self, prompt: str = "Monte-moi cette séquence", group: str | None = None, style: StyleParams | None = None,
              analyzer: Analyzer | None = None, on_section=None):
        d = parse_directive(prompt)
        if d.kind != "build":
            raise DirectiveError("ce n'est pas une demande de montage")
        groups = [group] if group else None
        self.analyze(analyzer, groups)
        st = style or StyleParams(target_s=d.target_s)
        shots = plan_sequence(list(self.rushes.values()), st, group)
        if not shots:
            raise DirectiveError("aucun plan retenu : importez des rushes et vérifiez la sélection de séquence")
        return generate_first_edit(self.session, shots, label=prompt, durations={m: r.duration for m, r in self.rushes.items()},
                                   on_section=on_section)

    def build_with_multicam(self, prompt: str = "Monte-moi cette séquence", section: str | None = None, style: StyleParams | None = None,
                            analyzer: Analyzer | None = None, source_factory=None, sync_cfg=None, on_section=None,
                            visual=None) -> dict:
        """Comme `build`, mais les rushes qui se chevauchent dans le temps (plusieurs caméras) sont regroupés, synchronisés par l'audio puis montés
        en MULTICAM avec choix d'angle automatique ; les autres rushes sont montés à plat. Tout, dans l'ordre chronologique, en UN geste d'annulation.
        Renvoie un compte rendu (groupes, lignes de synchro, angles écartés)."""
        from ..auto import multicam as M
        from ..sync.sources import FileSource
        d = parse_directive(prompt)
        if d.kind != "build":
            raise DirectiveError("ce n'est pas une demande de montage")
        self.analyze(analyzer, [section] if section else None)
        st = style or StyleParams(target_s=d.target_s)
        durs = {m: r.duration for m, r in self.rushes.items()}
        pool = [r for r in self.rushes.values() if section is None or r.group == section]
        props = M.propose_groups(pool, only_section=section)
        covered = {r.media for p in props for r in p.members}
        report = {"groups": [], "plain": 0}
        blocks = [("mc", p.start, p) for p in props]
        rest = sorted((r for r in pool if r.media not in covered), key=lambda r: (M._t(r) or 0.0, r.media))
        for r in rest:
            blocks.append(("plain", M._t(r) or 0.0, r))
        blocks.sort(key=lambda b: (b[1], b[0]))
        if not blocks:
            raise DirectiveError("aucun rush à monter")
        pos0 = max([c.end for c in self.tl.clips_on("V1")], default=0)
        with self.session.group(prompt, "auto", Scope(pos0, 10 ** 9)) as gid:
            i = 0
            while i < len(blocks):
                kind, _, obj = blocks[i]
                if kind == "plain":
                    run = [obj]
                    while i + 1 < len(blocks) and blocks[i + 1][0] == "plain":
                        i += 1
                        run.append(blocks[i][2])
                    shots = plan_sequence(run, st)
                    if shots:
                        generate_first_edit(self.session, shots, label=prompt, durations=durs, on_section=on_section)
                        report["plain"] += len(shots)
                else:
                    g = M.create_group(self.session, obj, durs)
                    lines = M.sync_group(self.session, g, self.project.root, sync_cfg, source_factory=source_factory or FileSource)
                    grp = self.tl.group(g)
                    skipped = [a.id for a in grp.angles if not a.usable]
                    moms = M.master_moments(self.rushes[obj.master], self.tl, g)
                    vp = visual if visual is not None else VisualParams()
                    stats = None
                    if moms:
                        g0, g1 = min(a for a, _ in moms), max(b for _, b in moms)
                        samples = None
                        if vp.enabled:
                            samples, stats = M.analyze_group(self.session, g, self.project.root, g0, g1, durs, vp,
                                                            self.project.data / "analysis", self._fresh_proxy)
                        picks = M.choose_angles(self.tl, g, g0, g1, durs, st, moms, samples, vp)
                        if picks:
                            M.generate_multicam_edit(self.session, g, picks, label=prompt)
                    report["groups"].append({"group": g, "name": obj.name, "angles": len(grp.angles), "skipped": skipped,
                                             "sync": lines, "shots": len(picks) if moms else 0,
                                             "analysis": None if stats is None else {"angles": stats.angles, "frames": stats.frames,
                                                                                    "seconds": round(stats.seconds, 2), "cache_hits": stats.cache_hits,
                                                                                    "peak_rss_mb": stats.peak_rss_mb, "notes": stats.notes}})
                    if on_section:
                        on_section(obj.name, 0, 0)
                i += 1
        return report

    def _fresh_proxy(self, media: str):
        from ..proxies import proxy_state
        orig, prox = self.project.root / media, self.project.proxy_path(media)
        try:
            return prox if proxy_state(orig, prox) == "ok" else None
        except OSError:
            return None

    def add_music(self, path: Path | str, gain_db: float = -14.0, fade_out_s: float = 3.0) -> str:
        """Pose une musique sous le montage (fichier lu en place, jamais copié ni modifié). Annulable ; modifiable comme tout plan audio."""
        from ..auto.music import place_music
        from ..probe import probe
        p = Path(path)
        p = p if p.is_absolute() else self.project.root / p
        info = probe(p)
        try:
            media = p.resolve().relative_to(self.project.root.resolve()).as_posix()
        except ValueError:
            media = str(p.resolve())
        return place_music(self.session, media, info.duration, gain_db, fade_out_s)

    def command(self, text: str, selection: Selection | None = None, style: StyleParams | None = None, analyzer: Analyzer | None = None
                ) -> DirectiveResult:
        d = parse_directive(text)
        if d.kind == "build":
            rep = self.build(text, None, style, analyzer)
            return DirectiveResult("build", rep.shots, 0, f"{rep.shots} plans posés sur {len(rep.sections)} section(s)")
        if selection is None:
            raise DirectiveError("sélectionnez d'abord une zone ou des plans sur la timeline")
        used = {c.media for c in self.tl.clips}
        cut = sorted(((m, r.duration) for m, r in self.rushes.items() if r.duration >= 1.0), key=lambda p: (p[0] in used, p[0]))   # rushes pas encore montés d'abord
        return apply_directive(self.session, d, selection, EditContext(cutaways=cut))

    # --- vie du projet ------------------------------------------------------------
    def undo(self) -> str:
        """Annule d'un geste la dernière génération/consigne (groupe), sinon le dernier pas manuel."""
        if self._last_is_group():
            return f"{len(self.session.undo_group())} pas annulés"
        return self.session.undo()

    def _last_is_group(self) -> bool:
        return bool(self.session._undo and self.session._undo[-1].group)

    def detect_format(self) -> dict:
        """Format de travail déduit des rushes : cadence dominante (arrondie, p. ex. 50) et plus grande résolution (4K si un rush l'est).
        Les rushes de tailles différentes (4K50 et 1080p50) cohabitent : chacun est mis à l'échelle à l'export."""
        from collections import Counter
        from ..probe import probe_cached
        fps, sizes = Counter(), []
        for r in self.rushes.values():
            try:
                i = probe_cached(self.project.root / r.media)
            except Exception:                              # noqa: BLE001 — rush illisible : ignoré ici, signalé à l'export
                continue
            if i.fps:
                fps[round(i.fps)] += 1
            if i.width and i.height:
                sizes.append((i.width, i.height))
        if not fps:
            return {}
        return {"fps": fps.most_common(1)[0][0], "resolution": max(sizes, key=lambda s: s[0] * s[1]) if sizes else None,
                "sizes": sorted(set(sizes)), "fps_seen": dict(fps)}

    def apply_format(self, fmt: dict | None = None) -> dict:
        """Règle cadence et résolution du projet. Refusé si la timeline contient déjà des plans (changer la cadence déplacerait les coupes)."""
        from ..timeline import SetProject, TimelineError
        fmt = fmt or self.detect_format()
        if not fmt:
            return {}
        if self.tl.clips and fmt.get("fps") != self.tl.fps:
            raise TimelineError("la timeline contient déjà des plans : la cadence ne peut plus changer")
        props = {"fps": int(fmt["fps"])}
        if fmt.get("resolution"):
            props["resolution"] = tuple(fmt["resolution"])
        if any(getattr(self.tl, k) != v for k, v in props.items()):
            self.session.do(SetProject(props), "format du projet")
        return props

    def export(self, out: Path | str | None = None, kind: str = "final", on_progress=None, cancel=None, resolution=None):
        """Exporte la timeline COURANTE en MP4 (toutes les pistes). Renvoie le rapport (durée, avertissements : rien n'est ignoré en silence)."""
        from ..render_timeline import render_timeline
        self.project.ensure()
        out = Path(out) if out else self.project.renders_dir / f"{self.tl.title or 'film'}.mp4"
        return render_timeline(self.tl, self.project.root, out, self.project.cache_dir, kind, on_progress, cancel, resolution)

    def save(self, name: str | None = None) -> Path:
        """Tout est déjà journalisé à chaque action ; `save` crée en plus un instantané nommé (jamais écrasé)."""
        return self.session.snapshot(name or "enregistrement")
