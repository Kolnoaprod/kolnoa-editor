# Édition manuelle sur la timeline Qt — rognage, rasoir, chutier, formes d'onde

Chaque fonction passe **exclusivement** par le moteur (`Session.do` d'une `Transaction`) : le widget ne modifie jamais le modèle,
un geste = une étape d'annulation, un refus du moteur laisse le modèle intact. PR distinctes, empilées, aucune fusion automatique.

## 1. Rognage des bords (PR trim)
- Moteur : `trim_limits(tl, clip, edge, linked, source_len)` donne la plage de `delta` permise ; `trim_edge(...)` applique (refus hors plage).
  Limites : durée ≥ 1 image et fondus qui tiennent, **pas de chevauchement avec le voisin de piste**, début du film, **poignées du rush**
  (`source_len(media)`, pas pour un plan multicam dont la matière dépend du groupe), plans liés (intersection des limites), plan/piste verrouillés (refus).
  Le rognage simple ne décale rien d'autre (ripple/roll existent déjà dans le moteur).
- Vue : poignée de 6 px à chaque bord (curseur ↔), aimantation sur bords/repères/tête de lecture, **prévisualisation** (fantôme + trait + « +N i · timecode »,
  orange « limite » quand on bute), Échap annule, le relâcher valide en UNE transaction. Les plans liés suivent. Rien n'est écrit pendant le glisser.
- Tests : `tests/test_trim.py` (moteur) et `tests/test_ui_timeline.py` (geste complet, limite, Échap, poignées source).

## 2. Rasoir (PR rasoir)
- Moteur inchangé : `razor()` (SplitClip) copie TOUT le plan : source, gain, notes, étiquette, routage audio, angle/groupe multicam ; le fondu d'entrée reste à
  gauche, le fondu de sortie à droite ; les moitiés de plans liés restent liées par paire ; pistes verrouillées épargnées (refus si nommées).
- Vue : `S` coupe à la tête de lecture les pistes des plans **sélectionnés** (leurs liés suivent), ou toutes les pistes déverrouillées sans sélection ;
  `Maj+S` coupe toutes les pistes. Rien à couper → refus signalé, modèle intact. `Ctrl+Z` annule, `Ctrl+Maj+Z` / `Ctrl+Y` rétablit (une coupe = une étape).
- Raccourcis : `ui/shortcuts.py`, défauts `S`, `Maj+S`, `Ctrl+Z`, `Ctrl+Maj+Z`, `Ctrl+Y` ; modifiables (`Shortcuts.set`, fichier JSON, conflits refusés,
  fichier abîmé → défauts). Un réglage d'interface n'entre jamais dans le montage.
