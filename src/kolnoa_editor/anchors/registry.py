"""Registre d'identité des médias (A1-1) : un `media_id` OPAQUE et IMMUABLE par média, indépendant du chemin.

Règles (voir docs/A1_ANCRES_PROPOSITION.md §2) :
  - `media_id` = UUID v4 attribué à l'enregistrement ; jamais calculé depuis le contenu, jamais réattribué, jamais supprimé.
  - Le chemin (`path_rel`) est un INDICE mutable ; l'historique des chemins est conservé (`locations`).
  - Un hash partiel (`quick_hash` : 3 Mio échantillonnés) n'est JAMAIS une preuve d'identité : il ne sert qu'à PROPOSER un
    rapprochement. Un déplacement n'est appliqué que par `confirm_relink` (décision humaine), jamais par `register`.
  - Même chemin + même taille + même mtime ⇒ même média (preuve d'usage). Même chemin mais contenu changé ⇒ NOUVEAU media_id,
    l'ancien passe en « remplacé » (ses ancres restent lisibles et deviennent `perdue`, jamais réassignées en silence).
  - Ce module ne lit ni n'écrit jamais un média : il ne reçoit que des métadonnées (taille, mtime, durée, cadence).
  - Écriture atomique (fichier temporaire voisin + remplacement). Un fichier corrompu est REFUSÉ, jamais réinitialisé en silence.
  - Les empreintes privées (`PRIVATE_KEYS`) ne sortent jamais dans un profil portable ni un export (`portable_view`)."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

SCHEMA = 1
PRESENT, MISSING, REPLACED = "present", "manquant", "remplacé"
PRIVATE_KEYS = frozenset({"path_rel", "size", "mtime_ns", "quick_hash", "locations", "duration_us", "fps"})


class RegistryError(ValueError):
    """Opération refusée ou fichier de registre invalide."""


def fps_to_rational(fps: float | None, max_den: int = 1001) -> str | None:
    """29,97 → « 30000/1001 », 25 → « 25/1 » ; None si inconnue. La cadence est stockée en rationnel, jamais en flottant."""
    if not fps or fps <= 0:
        return None
    for n in (24, 30, 48, 60, 120):          # cadences NTSC : n×1000/1001 (23,976 / 29,97 / 59,94…)
        if abs(fps - n * 1000 / 1001) < 0.005:
            return f"{n * 1000}/1001"
    f = Fraction(fps).limit_denominator(max_den)
    return f"{f.numerator}/{f.denominator}"


@dataclass
class MediaEntry:
    media_id: str
    path_rel: str                       # indice MUTABLE (chemin relatif au dossier du projet, séparateurs « / »)
    size: int
    mtime_ns: int = 0
    kind: str = "video"                 # 'video' | 'audio'
    duration_us: int | None = None
    fps: str | None = None              # rationnel « num/den »
    vfr: bool = False                   # cadence variable : les ancres sont refusées (non_supportée)
    quick_hash: str | None = None       # PRIVÉ ; aide au rapprochement, jamais une preuve
    locations: list[str] = field(default_factory=list)   # historique des chemins (le dernier = path_rel)
    status: str = PRESENT
    confirmed_by: str = "auto-chemin"   # 'auto-chemin' | 'utilisateur'

    @property
    def fps_fraction(self) -> Fraction | None:
        return Fraction(self.fps) if self.fps else None

    @property
    def integer_fps(self) -> bool:
        f = self.fps_fraction
        return f is None or f.denominator == 1

    def signature(self) -> tuple:
        return (self.size, self.duration_us, self.fps, self.quick_hash)


@dataclass(frozen=True)
class Candidate:
    media_id: str
    path_rel: str
    matched: tuple[str, ...]            # signaux concordants : 'taille', 'durée', 'cadence', 'empreinte'
    conflicting: tuple[str, ...]        # signaux contradictoires


@dataclass(frozen=True)
class RelinkProposal:
    state: str                          # 'aucun' | 'probable' | 'ambigu'
    candidates: tuple[Candidate, ...] = ()

    @property
    def media_id(self) -> str | None:
        """Le seul candidat d'un rapprochement `probable` — à PROPOSER, jamais à appliquer seul."""
        return self.candidates[0].media_id if self.state == "probable" else None


class MediaRegistry:
    def __init__(self, path: Path | str | None = None, id_factory: Callable[[], str] | None = None):
        self.path = Path(path) if path else None
        self._new_id = id_factory or (lambda: str(uuid.uuid4()))
        self.entries: dict[str, MediaEntry] = {}

    # --- persistance ---------------------------------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | str, id_factory: Callable[[], str] | None = None) -> "MediaRegistry":
        """Charge le registre (vide s'il n'existe pas encore). Un fichier illisible lève `RegistryError` : jamais de remise à zéro."""
        reg = cls(path, id_factory)
        p = Path(path)
        if not p.exists():
            return reg
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("schema") != SCHEMA:
                raise RegistryError(f"schéma de registre {data.get('schema')!r} non supporté")
            for d in data["media"]:
                e = MediaEntry(**d)
                if e.media_id in reg.entries:
                    raise RegistryError(f"media_id en double : {e.media_id}")
                reg.entries[e.media_id] = e
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RegistryError(f"registre illisible ({p.name}) : {exc}") from exc
        return reg

    def save(self) -> None:
        if self.path is None:
            raise RegistryError("registre sans chemin de fichier")
        doc = {"schema": SCHEMA, "media": [asdict(e) for e in self.entries.values()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".part")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- lecture -------------------------------------------------------------------------------------------------
    def get(self, media_id: str) -> MediaEntry:
        try:
            return self.entries[media_id]
        except KeyError:
            raise RegistryError(f"media_id inconnu : {media_id}") from None

    def id_for_path(self, path_rel: str) -> str | None:
        """`media_id` du média PRÉSENT à ce chemin (None si inconnu, manquant ou remplacé)."""
        for e in self.entries.values():
            if e.path_rel == path_rel and e.status == PRESENT:
                return e.media_id
        return None

    def path_for(self, media_id: str) -> str:
        return self.get(media_id).path_rel

    # --- enregistrement ----------------------------------------------------------------------------------------------
    def register(self, path_rel: str, size: int, mtime_ns: int = 0, kind: str = "video", duration_us: int | None = None,
                 fps: str | None = None, vfr: bool = False, quick_hash: str | None = None) -> MediaEntry:
        """Enregistre le média trouvé à `path_rel`.
        - même chemin, même taille, même mtime : même média (aucun changement d'identité) ;
        - même chemin, contenu changé : l'ancien devient « remplacé » (identité conservée pour ses ancres), nouveau `media_id` ;
        - chemin inconnu : nouveau `media_id`. JAMAIS d'assimilation à un autre média sous un autre chemin (voir `propose_relink`)."""
        path_rel = path_rel.replace("\\", "/")
        cur = next((e for e in self.entries.values() if e.path_rel == path_rel and e.status == PRESENT), None)
        if cur is not None:
            if (cur.size, cur.mtime_ns) == (size, mtime_ns):
                cur.kind = kind or cur.kind
                for name, val in (("duration_us", duration_us), ("fps", fps), ("quick_hash", quick_hash)):
                    if val is not None:
                        setattr(cur, name, val)
                cur.vfr = cur.vfr or vfr
                return cur
            cur.status = REPLACED
        entry = MediaEntry(media_id=self._new_id(), path_rel=path_rel, size=size, mtime_ns=mtime_ns, kind=kind,
                           duration_us=duration_us, fps=fps, vfr=vfr, quick_hash=quick_hash, locations=[path_rel])
        if entry.media_id in self.entries:
            raise RegistryError("id_factory a produit un media_id déjà utilisé")
        self.entries[entry.media_id] = entry
        return entry

    def mark_missing(self, present_paths: set[str]) -> list[str]:
        """Les médias PRÉSENTS dont le chemin n'est plus sur le disque passent en « manquant » (jamais supprimés)."""
        gone = []
        for e in self.entries.values():
            if e.status == PRESENT and e.path_rel not in present_paths:
                e.status = MISSING
                gone.append(e.media_id)
        return gone

    # --- rapprochement (déplacement / réimport) ------------------------------------------------------------------------
    def propose_relink(self, path_rel: str, size: int, duration_us: int | None = None, fps: str | None = None,
                       quick_hash: str | None = None) -> RelinkProposal:
        """Propose à quel média MANQUANT correspond un fichier trouvé à un nouveau chemin. NE MODIFIE RIEN.
        `probable` : un seul candidat, avec taille, durée, cadence ET empreinte tous connus et concordants.
        `ambigu` : plusieurs candidats, ou un seul dont un signal manque ou est contradictoire (ex. même empreinte, durée différente).
        Un candidat dont la taille diffère sans autre signal commun n'est pas retenu."""
        path_rel = path_rel.replace("\\", "/")
        cands: list[Candidate] = []
        for e in self.entries.values():
            if e.status != MISSING:
                continue
            matched, conflict = [], []
            for name, mine, theirs in (("taille", e.size, size), ("durée", e.duration_us, duration_us),
                                       ("cadence", e.fps, fps), ("empreinte", e.quick_hash, quick_hash)):
                if mine is None or theirs is None:
                    continue
                (matched if mine == theirs else conflict).append(name)
            if matched:
                cands.append(Candidate(e.media_id, e.path_rel, tuple(matched), tuple(conflict)))
        if not cands:
            return RelinkProposal("aucun")
        full = [c for c in cands if not c.conflicting and len(c.matched) == 4]
        if len(cands) == 1 and len(full) == 1:
            return RelinkProposal("probable", tuple(cands))
        return RelinkProposal("ambigu", tuple(sorted(cands, key=lambda c: (-len(c.matched), c.media_id))))

    def confirm_relink(self, media_id: str, new_path_rel: str, by: str = "utilisateur") -> MediaEntry:
        """Applique un rapprochement APRÈS décision humaine : le média garde son `media_id`. Refusé si le chemin cible appartient déjà
        à un autre média présent (collision) ou si le média n'est pas « manquant »."""
        new_path_rel = new_path_rel.replace("\\", "/")
        e = self.get(media_id)
        if e.status != MISSING:
            raise RegistryError(f"{media_id} n'est pas manquant : rien à rapprocher")
        owner = self.id_for_path(new_path_rel)
        if owner is not None and owner != media_id:
            raise RegistryError(f"collision : {new_path_rel} appartient déjà au média {owner}")
        e.path_rel, e.status, e.confirmed_by = new_path_rel, PRESENT, by
        if new_path_rel not in e.locations:
            e.locations.append(new_path_rel)
        return e

    # --- export ------------------------------------------------------------------------------------------------------
    def portable_view(self) -> dict[str, Any]:
        """Vue SANS aucune empreinte privée ni chemin : les `media_id` sont remplacés par des indices locaux (`media-001`…)."""
        index = {mid: f"media-{n:03d}" for n, mid in enumerate(sorted(self.entries), 1)}
        return {"schema": SCHEMA, "media": [{"media": index[mid], "kind": e.kind, "status": e.status}
                                            for mid, e in sorted(self.entries.items())]}


def register_source_file(reg: MediaRegistry, sf: Any, probe: Any = None) -> MediaEntry:
    """Enregistre un `scanner.SourceFile` (et, si disponible, son `ProbeInfo`) — métadonnées seulement, aucune lecture du média."""
    duration = sf.duration if sf.duration is not None else getattr(probe, "duration", None)
    return reg.register(sf.rel, sf.size, sf.mtime_ns, sf.kind,
                        duration_us=round(duration * 1_000_000) if duration is not None else None,
                        fps=fps_to_rational(getattr(probe, "base_fps", None) or getattr(probe, "fps", None)),
                        vfr=bool(getattr(probe, "is_vfr", False)), quick_hash=sf.quick_hash)
