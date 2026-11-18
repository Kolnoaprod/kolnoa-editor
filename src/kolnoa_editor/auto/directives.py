"""Consignes ciblées en français, interprétées par règles LOCALES (aucune API) puis appliquées sur la sélection, avec les mêmes
primitives et la même Session que la souris. Rien hors de la zone choisie n'est touché ; un groupe = un geste d'annulation.

Consignes comprises (v0) : « monte-moi cette séquence [en N min] », « raccourcis [cette partie] [de N s | à N s | de moitié | de N %] »,
« accélère le rythme [×N] », « remplace par un plan de coupe »."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Callable

from ..timeline import Scope, Selection, Session, TimelineError
from ..timeline.chutier import place_rush
from ..timeline.editing import ripple_trim
from ..timeline.model import Track
from ..timeline import AddTrack

MIN_SHOT_FRAMES_S = 1.0


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    return re.sub(r"[̀-ͯ]", "", t).replace("’", "'")


@dataclass(frozen=True)
class Directive:
    kind: str                        # build | shorten | faster | cutaway
    seconds: float | None = None     # raccourcir DE n s (cut) ou durée visée (target)
    target_s: float | None = None
    ratio: float | None = None       # part retirée (0..1) pour shorten, facteur de rythme pour faster
    text: str = ""


class DirectiveError(TimelineError):
    pass


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_directive(text: str) -> Directive:
    t = _norm(text)
    if re.search(r"\bmonte\b|\bmonter\b|\bmontage\b.*(auto|premier)", t):
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(min|minute|minutes|s|sec|secondes?)\b", t)
        tgt = None
        if m:
            tgt = _num(m.group(1)) * (60 if m.group(2).startswith("min") else 1)
        return Directive("build", target_s=tgt, text=text)
    if "plan de coupe" in t or "plan d'ambiance" in t or "cutaway" in t:
        return Directive("cutaway", text=text)
    if re.search(r"\bacc[eé]l[eè]re|rythme (plus )?(rapide|dynamique|nerveux)|plus (rapide|dynamique)", t):
        m = re.search(r"(?:x|×|par)\s*(\d+(?:[.,]\d+)?)", t)
        return Directive("faster", ratio=_num(m.group(1)) if m else 1.5, text=text)
    if re.search(r"\braccourci|\braccourcir|\bcoupe\b|\bplus court", t):
        if "moitie" in t:
            return Directive("shorten", ratio=0.5, text=text)
        m = re.search(r"de (\d+(?:[.,]\d+)?)\s*%", t)
        if m:
            return Directive("shorten", ratio=_num(m.group(1)) / 100, text=text)
        m = re.search(r"(de|a)\s+(\d+(?:[.,]\d+)?)\s*(min|minute|minutes|s|sec|secondes?)\b", t)
        if m:
            v = _num(m.group(2)) * (60 if m.group(3).startswith("min") else 1)
            return Directive("shorten", seconds=v if m.group(1) == "de" else None, target_s=v if m.group(1) == "a" else None, text=text)
        return Directive("shorten", ratio=0.2, text=text)
    raise DirectiveError(f"consigne non comprise : « {text} » (v0 : monter, raccourcir, accélérer le rythme, plan de coupe)")


@dataclass
class EditContext:
    video: str = "V1"
    audio: str = "A1"
    cutaway_track: str = "V2"
    cutaways: list[tuple[str, float]] = field(default_factory=list)       # (média, durée) candidats plans de coupe
    source_len: Callable[[str], float | None] | None = None


@dataclass
class DirectiveResult:
    kind: str
    changed: int
    frames_delta: int
    message: str


def _selected_video(session: Session, sel: Selection, ctx: EditContext):
    sel.require_fresh(session.tl)
    ids = sel.clips_in(session.tl)
    clips = [session.tl.clip(i) for i in ids if session.tl.clip(i).track == ctx.video]
    if not clips:
        raise DirectiveError("aucun plan vidéo dans la sélection")
    valid = [c.id for c in clips if session.state_of(c.id) == "valide"]
    if valid:
        raise DirectiveError(f"la sélection contient des plans validés ({', '.join(valid[:3])}) : retirez-les ou annulez la validation")
    return sorted(clips, key=lambda c: c.start)


def _shorten_plan(clips, cut_frames: int, fps: int) -> dict[str, int]:
    """Répartit `cut_frames` en prélevant sur les plans les plus longs d'abord, sans descendre sous 1 s."""
    floor = round(MIN_SHOT_FRAMES_S * fps)
    room = {c.id: max(0, c.duration - floor) for c in clips}
    plan, left = {}, cut_frames
    while left > 0 and any(room.values()):
        order = sorted((c for c in clips if room[c.id] > 0), key=lambda c: (-(c.duration - plan.get(c.id, 0)), c.start))
        c = order[0]
        take = min(room[c.id], max(1, left // len(order)))
        plan[c.id] = plan.get(c.id, 0) + take
        room[c.id] -= take
        left -= take
    return plan


def _apply_trims(session: Session, plan: dict[str, int], scope: Scope, label: str) -> int:
    done = 0
    with session.group(label, "directive", scope):
        # de la fin vers le début : les décalages en cascade n'invalident pas les plans restants à traiter
        for cid in sorted(plan, key=lambda i: -session.tl.clip(i).start):
            if plan[cid] <= 0:
                continue
            tx = ripple_trim(session.tl, cid, "tail", plan[cid])
            session.do(tx, f"{label} : {cid}", confirmed_protected=True)
            done += 1
    return done


def apply_directive(session: Session, d: Directive, sel: Selection, ctx: EditContext | None = None) -> DirectiveResult:
    ctx = ctx or EditContext()
    tl = session.tl
    fps = tl.fps
    if d.kind == "build":
        raise DirectiveError("« monter » se lance depuis le contrôleur de projet (il connaît les rushes)")
    clips = _selected_video(session, sel, ctx)
    total = sum(c.duration for c in clips)
    s0, s1 = clips[0].start, max(c.end for c in clips)
    ripple_scope = Scope(s0, s1 + 10 ** 9, None, ripple=True)       # la suite de la piste peut être décalée, jamais modifiée
    if d.kind in ("shorten", "faster"):
        if d.kind == "faster":
            cut = round(total * (1 - 1 / max(d.ratio or 1.5, 1.01)))
        elif d.seconds is not None:
            cut = round(d.seconds * fps)
        elif d.target_s is not None:
            cut = total - round(d.target_s * fps)
        else:
            cut = round(total * (d.ratio or 0.2))
        if cut <= 0:
            return DirectiveResult(d.kind, 0, 0, "rien à retirer")
        plan = _shorten_plan(clips, cut, fps) if d.kind == "shorten" else {
            c.id: min(max(0, c.duration - round(MIN_SHOT_FRAMES_S * fps)), round(c.duration * (1 - 1 / max(d.ratio or 1.5, 1.01))))
            for c in clips}
        n = _apply_trims(session, plan, ripple_scope, "raccourcir" if d.kind == "shorten" else "accélérer le rythme")
        got = sum(plan.values())
        return DirectiveResult(d.kind, n, -got, f"{n} plan(s) raccourci(s), {got / fps:.1f} s retirées")
    if d.kind == "cutaway":
        pool = [c for c in ctx.cutaways]
        if not pool:
            raise DirectiveError("aucun plan de coupe disponible (importez des rushes d'ambiance)")
        have = {t.id for t in tl.tracks}
        if ctx.cutaway_track not in have:
            session.do(AddTrack(asdict(Track(ctx.cutaway_track, "video", "main"))), f"piste {ctx.cutaway_track}")
        scope = Scope(s0, s1, (ctx.cutaway_track,))
        placed, k = 0, 0
        with session.group("plan de coupe", "directive", scope):
            for c in clips:
                cands = [p for p in pool if p[0] != c.media]
                if not cands:
                    continue
                media, dur = cands[k % len(cands)]
                k += 1
                frames = min(c.duration, round(min(dur, 4.0) * fps))
                if frames < 1:
                    continue
                at = c.start + (c.duration - frames) // 2
                tx = place_rush(session.tl, media, frames / fps, ctx.cutaway_track, at, 0.0, "overwrite", None, None, "video", "", dur)
                session.do(tx, f"plan de coupe sur {c.id}")
                placed += 1
        return DirectiveResult("cutaway", placed, 0, f"{placed} plan(s) de coupe posé(s) sur {ctx.cutaway_track}")
    raise DirectiveError(f"consigne inconnue : {d.kind}")
