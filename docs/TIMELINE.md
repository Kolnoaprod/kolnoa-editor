# Timeline canonique (PR A)

Code : `src/kolnoa_editor/timeline/`. Spécification : `docs/ARCHITECTURE_V2.md` §2-3.
La liste de décisions reste le format d'échange avec l'IA ; la timeline est l'état éditable sur lequel
travailleront la souris, le clavier et les prompts (PR C et E) **sans jamais se contredire**.

## Modèle (schéma 1)

- **Temps** : positions et durées de timeline en **images entières** ; temps source et fondus en secondes
  (`timebase.py` est le seul endroit qui convertit). Aucun raccord ne dérive.
- `Track` (vidéo/audio, rôle, verrou, muet, gain), `Clip` (id `s001`…, piste, média, début, durée, `src_in`,
  audio, fondus, gain, note, étiquette, verrou, `parent`, `extra` réservé aux transitions/effets/étalonnage),
  `Marker` (`note`, `beat`, `phrase`, `sync`, `chapter`, `keep` = zone à ne pas couper).
- Les ids de plans gardent la forme de la liste de décisions : retours, versions et filiations restent valables.
  Un id supprimé est **retiré à vie** (`retired`) ; seule l'annulation d'une suppression le rend à son plan.
- `Timeline.validate()` : ids uniques, pistes connues, images entières, pas de chevauchement sur une piste,
  fondus qui tiennent dans le plan, schéma connu. `migrate()` : chaîne de migrations par schéma (vide en schéma 1,
  un schéma plus récent est refusé plutôt que mal lu).

## Opérations

`add_track`, `remove_track`, `add_clip`, `remove_clip`, `move_clip`, `trim_clip`, `split_clip` (↔ `join_clips`),
`shift_clips`, `set_clip_props`, `set_track_props`, `add_marker`, `remove_marker`, `set_project`, `transaction`.
Constructeurs : `ripple_delete`, `trim_head`, `trim_tail`.

- `apply(tl)` modifie et **renvoie l'inverse** ; `describe()` donne le diff lisible.
- `Transaction` = un seul pas d'annulation, tout ou rien (rollback si une opération échoue).
- `execute(tl, op)` valide ensuite la timeline et revient en arrière si elle est devenue invalide.
- Les **verrous** (plan, piste) refusent toute modification ; on peut toujours déverrouiller.
- Une opération refusée ne laisse aucune trace (ni état, ni journal).

## Session et journal

Dossier `<données>/timeline/` : `base.json` (état de départ, jamais modifié), `history.jsonl` (une ligne par
action `do`/`undo`/`redo`, écrite et synchronisée sur disque avant acquittement), `current.json` (état courant,
écriture atomique), `snapshots/` (instantanés nommés, jamais écrasés).
Ouvrir = relire la base et rejouer le journal : état **et** piles annuler/rétablir reviennent identiques après un
redémarrage. Une dernière ligne tronquée par un crash est ignorée ; une ligne corrompue au milieu est signalée.

## Passerelle liste de décisions

- `from_decision_list` : importe un montage (1 piste `V1`, plans bout à bout, durées en images comme le moteur).
- `to_decision_list` : exporte les montages « simples » ; **refuse** (erreur claire) trou, plusieurs pistes vidéo,
  plan hors piste principale ; **avertit** de ce qui ne s'exporte pas (repères, gain de plan, `extra`).
- Rendu : `timeline-render` = export + moteur actuel. Vérifié par test : même nombre d'images que le rendu direct.
- Aller-retour sans perte pour les durées alignées sur l'image ; idempotent sinon (durée hors grille arrondie à
  l'image, comme le moteur le faisait déjà).

## Hors périmètre (PR suivantes)

Multicam et synchronisation (B), interface (C), vitesse/effets/LUT/transitions/mixage (D), prompts et montage
automatique (E). Les champs `extra` et les repères sont déjà là pour les accueillir sans changer le schéma 1.
