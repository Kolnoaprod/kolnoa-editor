# Moteur de montage (PR C2a)

Fonctions de `kolnoa_editor.timeline` communes à l'interface manuelle, à la CLI et aux prompts. Chacune renvoie une `Transaction` : **un seul pas d'annulation, tout ou rien**, journalisée dans la session (annuler/rétablir, fermeture et réouverture vérifiées).

| Besoin | Fonction | Règle |
| --- | --- | --- |
| Lame de rasoir | `razor(frame, tracks=None, linked=True)` | coupe image **et** son liés ; pistes verrouillées épargnées (toutes pistes) ou refusées (piste nommée) ; moitiés droites reliées entre elles |
| Lier / dissocier | `link_clips`, `unlink_clips` | un plan par piste ; `linked=False` pour agir sur un seul plan |
| Poser un plan | `place_clip(clip, "overwrite" \| "insert", ripple_tracks=)` | `overwrite` (défaut) recouvre sans rien décaler ; `insert` décale la suite de la piste visée (et des pistes demandées) |
| Déplacer | `move_clips(id, start, track=None, linked=True, mode="refuse" \| "overwrite")` | déplacer un plan de coupe sur une autre piste ne bouge **aucun** autre plan ; chevauchement refusé par défaut |
| Remplacer | `replace_clip(id, media, src_in)` | même place, même durée, autre source ; refusé pour un plan multicam (utiliser `switch_angle`) |
| Raccourcir un passage | `delete_range(start, end, tracks=None, ripple=False)` | coupe et retire la plage ; avec `ripple` la suite avance partout, donc le montage reste synchrone |
| Rogner un bord | `ripple_trim(id, "head" \| "tail", delta, source_len=)` | la suite de la piste suit ; poignée source vérifiée |
| Compenser les voisins | `roll_trim(gauche, droite, delta)` | la coupe se déplace, durée totale et reste du montage inchangés |
| Magnétisme | `snap_points`, `snap_frame`, `snap_move` | bords des plans, repères, tête de lecture ; jamais sur ses propres bords ni ceux de ses liés |

## Garde-fous
- **Pas de désynchronisation silencieuse** : un ripple ou une insertion qui décalerait une piste verrouillée portant des plans est refusé.
- Plan ou piste verrouillés : refus, et la transaction entière est annulée.
- Les bords coupés par un recouvrement n'ont pas de fondu ; la continuité de la source est conservée (le plan recoupé reprend au bon instant).
- Un id supprimé n'est jamais réutilisé (règle du modèle) : rétablir une action passe par son inverse, jamais par un rejeu.

## Pas encore fait
- Mode « remplacement par glisser » sur une plage, plusieurs sources audio par séquence, enregistrement des changements d'angle aux touches 1 à 9 (C3), sous-clips I/O du chutier (C3), interface (C2b/C3).
- Les durées de rushes (`source_len`) sont fournies par l'appelant : le moteur ne lit aucun média.
