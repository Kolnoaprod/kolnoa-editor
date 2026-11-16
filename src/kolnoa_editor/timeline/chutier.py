"""Chutier : repérer des plans courts (I / O) dans les rushes, puis les glisser sur la timeline.

Le chutier ne lit aucun média et n'en modifie aucun : un plan du chutier est une plage [entrée, sortie] en secondes
d'un rush existant. `place_subclip` en fait un vrai plan de la timeline avec les mêmes modes que le reste du montage
(recouvrement par défaut : rien d'autre ne bouge ; insertion avec décalage sur demande).
"""

from __future__ import annotations

from .editing import _Ids, place_clip
from .model import Clip, Timeline, TimelineError
from .ops import AddSubclip, RemoveSubclip, SetSubclip, Transaction, execute


def mark_subclip(tl: Timeline, media: str, src_in: float, src_out: float, name: str = "", kind: str = "video",
                 tags: list[str] | None = None, note: str = "", source_len: float | None = None) -> AddSubclip:
    """Touches I puis O : enregistre la plage [src_in, src_out]. `source_len` (durée du rush, connue de l'appelant)
    permet de refuser une sortie au-delà de la fin du média."""
    if not (0 <= src_in < src_out):
        raise TimelineError(f"plage I/O invalide [{src_in}, {src_out}] : l'entrée doit précéder la sortie")
    if source_len is not None and src_out > source_len + 1e-6:
        raise TimelineError(f"la sortie {src_out:.2f} s dépasse la fin du média ({source_len:.2f} s)")
    sid = tl.next_subclip_id()
    return AddSubclip({"id": sid, "media": media, "src_in": round(src_in, 6), "src_out": round(src_out, 6),
                       "name": name or sid, "kind": kind, "tags": list(tags or []), "note": note})


def adjust_subclip(tl: Timeline, sub_id: str, src_in: float | None = None, src_out: float | None = None,
                   name: str | None = None, tags: list[str] | None = None, note: str | None = None) -> SetSubclip:
    """Re-règle l'entrée, la sortie ou les infos d'un plan du chutier (les plans déjà posés ne changent pas)."""
    b = tl.subclip(sub_id)
    props = {k: v for k, v in (("src_in", src_in), ("src_out", src_out), ("name", name), ("tags", tags), ("note", note))
             if v is not None}
    if not props:
        raise TimelineError("rien à modifier")
    lo, hi = props.get("src_in", b.src_in), props.get("src_out", b.src_out)
    if not (0 <= lo < hi):
        raise TimelineError(f"plage I/O invalide [{lo}, {hi}]")
    return SetSubclip(sub_id, props)


def remove_subclip(tl: Timeline, sub_id: str) -> RemoveSubclip:
    tl.subclip(sub_id)
    return RemoveSubclip(sub_id)


def search_bin(tl: Timeline, text: str = "", tag: str | None = None, media: str | None = None) -> list[str]:
    """Ids des plans du chutier qui contiennent `text` (nom ou note), portent l'étiquette `tag`, viennent de `media`."""
    t = text.lower()
    return [b.id for b in tl.bin
            if (not t or t in b.name.lower() or t in b.note.lower())
            and (tag is None or tag in b.tags) and (media is None or b.media == media)]


def place_subclip(tl: Timeline, sub_id: str, track: str, start: int, mode: str = "overwrite",
                  audio_track: str | None = None, ripple_tracks: list[str] | None = None) -> Transaction:
    """Glisse un plan du chutier sur `track` à l'image `start`. Avec `audio_track`, son son (même rush) est posé en
    même temps sur cette piste audio et lié à l'image. Un seul pas d'annulation."""
    b = tl.subclip(sub_id)
    frames = max(1, round(b.length * tl.fps))
    kind = tl.track(track).kind
    if kind != b.kind:
        raise TimelineError(f"{b.id} est un plan {b.kind} : il ne se pose pas sur la piste {track} ({kind})")
    if audio_track is not None:
        if b.kind != "video":
            raise TimelineError("un plan audio n'a pas de son à lier")
        if tl.track(audio_track).kind != "audio":
            raise TimelineError(f"{audio_track} n'est pas une piste audio")
    ids = _Ids(tl)
    link = ids.link() if audio_track else None
    vid = Clip(ids.clip(), track, b.media, start, frames, b.src_in, audio="source" if not audio_track else "mute",
               label=b.name, link=link)
    work = tl.copy()
    first = place_clip(work, vid, mode, ripple_tracks, ids)
    execute(work, first)
    parts = [first]
    if audio_track:
        snd = Clip(ids.clip(), audio_track, b.media, start, frames, b.src_in, audio="source", label=b.name, link=link)
        parts.append(place_clip(work, snd, mode, ripple_tracks, ids))
        execute(work, parts[-1])
    return Transaction(parts, f"chutier {b.id} → {track} à l'image {start}")
