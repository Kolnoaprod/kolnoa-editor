"""Validation avant rendu : on signale tout problème AVANT de lancer des heures d'encodage.

Niveaux : `error` bloque le rendu ; `warning` est à lire ; `info` explique un choix automatique.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .decision_list import DecisionList, DecisionListError
from .ffmpeg import FFmpegError, ffmpeg_bin, ffprobe_bin, has_nvenc
from .probe import probe_cached
from .project import Project
from .proxies import proxy_state

RANGE_TOLERANCE = 0.05          # secondes
FINAL_BYTES_PER_SEC = 2_500_000  # ~20 Mb/s
PROXY_BYTES_PER_SEC = 500_000    # ~4 Mb/s
LONG_PATH = 240                  # limite Windows MAX_PATH = 260


@dataclass
class Issue:
    level: str        # 'error' | 'warning' | 'info'
    code: str
    message: str
    segment: str | None = None

    def __str__(self) -> str:
        where = f" [{self.segment}]" if self.segment else ""
        return f"{self.level.upper():7s} {self.code}{where}: {self.message}"


class PreflightError(ValueError):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        errors = [i for i in issues if i.level == "error"]
        super().__init__(
            f"{len(errors)} problème(s) bloquant(s) avant rendu :\n" + "\n".join(f"  - {i}" for i in errors)
        )


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)


def format_issues(issues: list[Issue]) -> str:
    return "\n".join(str(i) for i in issues) if issues else "Aucun problème détecté."


def _existing_ancestor(path: Path) -> Path:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def preflight(
    project: Project,
    dl: DecisionList,
    kind: str = "proxy",
    out_dir: Path | None = None,
) -> list[Issue]:
    issues: list[Issue] = []

    # 1. outils
    try:
        ffmpeg_bin()
        ffprobe_bin()
    except FFmpegError as exc:
        return [Issue("error", "FFMPEG_MISSING", str(exc))]
    if kind == "final" and not has_nvenc():
        issues.append(Issue("warning", "NVENC_ABSENT",
                            "Encodage GPU indisponible : le rendu final utilisera le CPU (plus lent)."))

    # 2. structure de la liste (ids, plages, fondus)
    if not dl.segments:
        return issues + [Issue("error", "EMPTY", "Aucun segment à rendre.")]
    try:
        dl.validate()
    except DecisionListError as exc:
        return issues + [Issue("error", "INVALID_LIST", str(exc))]

    # 3. sources
    probed: dict[str, object] = {}
    for seg in dl.segments:
        try:
            path = project.resolve(seg.clip)
        except ValueError:
            issues.append(Issue("error", "PATH_OUTSIDE", f"chemin hors du dossier du mariage : {seg.clip}", seg.id))
            continue
        if not path.is_file():
            issues.append(Issue("error", "SRC_MISSING", f"fichier introuvable : {seg.clip}", seg.id))
            continue
        if path.stat().st_size == 0:
            issues.append(Issue("error", "SRC_EMPTY", f"fichier vide : {seg.clip}", seg.id))
            continue
        if len(str(path)) > LONG_PATH:
            issues.append(Issue("warning", "PATH_LONG", f"chemin de {len(str(path))} caractères (limite Windows 260)", seg.id))
        if seg.clip not in probed:
            try:
                probed[seg.clip] = probe_cached(path)
            except (FFmpegError, ValueError, OSError) as exc:
                probed[seg.clip] = None
                issues.append(Issue("error", "SRC_UNREADABLE", f"{seg.clip} : {str(exc).splitlines()[0]}", seg.id))
        info = probed[seg.clip]
        if info is None:
            continue
        if info.width is None:
            issues.append(Issue("error", "NO_VIDEO", f"aucune piste vidéo : {seg.clip}", seg.id))
            continue
        if seg.end > info.duration + RANGE_TOLERANCE:
            issues.append(Issue("error", "RANGE_BEYOND",
                                f"plage {seg.start:.2f}-{seg.end:.2f}s au-delà de la durée du clip ({info.duration:.2f}s)",
                                seg.id))
        if info.rotation or info.display_size[1] > info.display_size[0]:
            issues.append(Issue("warning", "ORIENTATION",
                                "clip vertical ou pivoté : il sera centré avec des bandes noires.", seg.id))
        if info.is_vfr:
            issues.append(Issue("warning", "VFR",
                                f"cadence variable ({info.fps:.1f} i/s moyenne, base {info.base_fps:.1f}) : "
                                "risque de désynchro son/image, le rendu force une cadence fixe.", seg.id))
        elif info.fps and abs(info.fps - dl.fps) > 0.05:
            ratio = info.fps / dl.fps
            if ratio >= 1.9 and abs(ratio - round(ratio)) < 0.02:
                issues.append(Issue("info", "FPS_DECIMATION",
                                    f"{info.fps:g} → {dl.fps} i/s : une image sur {round(ratio)} est conservée.", seg.id))
            else:
                issues.append(Issue("warning", "FPS_CONVERT",
                                    f"{info.fps:g} → {dl.fps} i/s : conversion non entière, saccades possibles.", seg.id))
        if kind == "final" and info.display_size and info.display_size[0] < dl.resolution[0] * 0.9 \
                and info.display_size[1] < dl.resolution[1] * 0.9:
            issues.append(Issue("warning", "UPSCALE",
                                f"source {info.display_size[0]}x{info.display_size[1]} agrandie vers {dl.resolution[0]}x{dl.resolution[1]}.", seg.id))
        if seg.audio == "source" and not info.has_audio:
            issues.append(Issue("info", "NO_AUDIO", f"{seg.clip} n'a pas de son : piste silencieuse.", seg.id))
        if kind == "proxy" and proxy_state(path, project.proxy_path(seg.clip)) != "ok":
            issues.append(Issue("warning", "PROXY_MISSING",
                                "proxy absent ou périmé : l'original sera lu (lent). Lance `kolnoa proxies`.", seg.id))

    # 4. musique
    if dl.music is not None:
        try:
            mpath = project.resolve(dl.music.path)
        except ValueError:
            mpath = None
            issues.append(Issue("error", "PATH_OUTSIDE", f"musique hors du dossier : {dl.music.path}"))
        if mpath is not None:
            if not mpath.is_file():
                issues.append(Issue("error", "MUSIC_MISSING", f"musique introuvable : {dl.music.path}"))
            else:
                try:
                    minfo = probe_cached(mpath)
                    if not minfo.has_audio:
                        issues.append(Issue("error", "MUSIC_NO_AUDIO", f"{dl.music.path} ne contient pas d'audio."))
                    elif dl.music.start + dl.duration > minfo.duration + 0.5:
                        issues.append(Issue("warning", "MUSIC_SHORT",
                                            f"la musique ({minfo.duration:.0f}s) finit avant le film ({dl.duration:.0f}s)."))
                except (FFmpegError, ValueError, OSError) as exc:
                    issues.append(Issue("error", "MUSIC_UNREADABLE", str(exc).splitlines()[0]))

    # 5. espace disque : cache de segments + assemblage + fichier final ≈ 3 × le film
    per_sec = FINAL_BYTES_PER_SEC if kind == "final" else PROXY_BYTES_PER_SEC
    needed = int(dl.duration * per_sec * 3)
    for label, directory in (("dossier de données", project.data), ("dossier de sortie", out_dir or project.renders_dir)):
        try:
            free = shutil.disk_usage(_existing_ancestor(directory)).free
        except OSError:
            continue
        if free < needed:
            issues.append(Issue("error", "DISK_SPACE",
                                f"{label} : {free / 1e9:.1f} Go libres, {needed / 1e9:.1f} Go nécessaires."))
        elif free < needed * 2:
            issues.append(Issue("warning", "DISK_LOW",
                                f"{label} : {free / 1e9:.1f} Go libres pour {needed / 1e9:.1f} Go nécessaires."))
    return issues
