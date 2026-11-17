# Édition manuelle sur la timeline Qt — rognage, rasoir, chutier, formes d'onde

Chaque fonction passe **exclusivement** par le moteur (`Session.do` d'une `Transaction`) : le widget ne modifie jamais le modèle,
un geste = une étape d'annulation, un refus du moteur laisse le modèle intact. PR distinctes, empilées, aucune fusion automatique.

## 1. Rognage des bords (PR #20)
- Moteur : `trim_limits(tl, clip, edge, linked, source_len)` donne la plage de `delta` permise ; `trim_edge(...)` applique (refus hors plage).
  Limites : durée ≥ 1 image (et fondus qui tiennent), **pas de chevauchement avec le voisin de piste**, début du film, **poignées du rush**
  (`source_len(media)`, pas pour un plan multicam dont la matière dépend du groupe), plans liés (intersection des limites), plan/piste verrouillés (refus).
  Le rognage simple ne décale rien d'autre.
- **Alt + glisser = ripple** (arbitrage de revue) : `ripple_limits` / `ripple_edge`. La suite de la piste (et des pistes des plans liés) suit ; pas de limite de
  voisin ; restent durée ≥ 1 image + fondus, poignées du rush, liés, verrous. Aperçu : étiquette `RIPPLE`, fantômes orange des plans qui bougent, et **validation du moteur**
  pendant le geste (essai sur copie, une fois par valeur de delta ; erreur affichée en rouge) puis revalidation à l'application (`Session.do` atomique). Alt peut être pressé/relâché en cours de geste.
- Vue : poignée de 6 px à chaque bord (curseur ↔), aimantation sur bords/repères/tête de lecture, prévisualisation (fantôme + trait + « +N i · timecode », orange « limite »
  quand on bute), Échap annule, le relâcher valide en UNE transaction. Les plans liés suivent. Rien n'est écrit pendant le glisser.
- Tests : `tests/test_trim.py` (moteur, ripple compris) et `tests/test_ui_timeline.py` (geste complet, limite, Échap, poignées source, Alt+glisser + annulation).

## 2. Rasoir (PR #21)
- Moteur inchangé : `razor()` (SplitClip) copie TOUT le plan : source, gain, notes, étiquette, routage audio, angle/groupe multicam ; le fondu d'entrée reste à
  gauche, le fondu de sortie à droite ; les moitiés de plans liés restent liées par paire ; pistes verrouillées épargnées (refus si nommées).
- Vue : `S` coupe à la tête de lecture les pistes des plans **sélectionnés** (leurs liés suivent), ou toutes les pistes déverrouillées sans sélection ;
  `Maj+S` coupe toutes les pistes. Rien à couper → refus signalé, modèle intact. `Ctrl+Z` annule, `Ctrl+Maj+Z` / `Ctrl+Y` rétablit (une coupe = une étape).
- Raccourcis : `ui/shortcuts.py`, défauts `S`, `Maj+S`, `Ctrl+Z`, `Ctrl+Maj+Z`, `Ctrl+Y` ; modifiables (`Shortcuts.set`, fichier JSON, conflits refusés,
  fichier abîmé → défauts). Un réglage d'interface n'entre jamais dans le montage.

## 3. Glisser depuis le chutier (PR #22)
- Moteur : `place_subclip` (existant) et `place_rush(media, durée, piste, image, src_in, mode, …)` pour un rush brut — même code (`_place`), aucune entrée de chutier créée,
  plage bornée par la durée du rush.
- `ui/bin_view.py` : liste des plans du chutier, glisser (QDrag). `ui/dnd.py` : charge `{"sub": id}` ou `{"media", "length", "src_in", "kind"}` — que des références,
  jamais de chemin absolu ; charge étrangère/abîmée ignorée.
- `TimelineView.plan_drop` évalue un dépôt **sans rien modifier** : la validité vient du moteur (essai de la transaction) → piste verrouillée, type de piste, plan
  verrouillé dessous, ripple refusé, fin du rush : raison affichée en rouge dans l'aperçu.
- Aperçu : fantôme vert (valide) / rouge (refusé, avec la raison), trait bleu = insertion. Aimantation sur bords, repères, tête de lecture.
- Modes : `drop_mode` (`overwrite` par défaut, ou `insert`), **Ctrl pendant le glisser inverse le mode**. L'insertion décale la suite de la piste visée seulement
  (« insertion sur toutes les pistes » : reportée, arbitrage de revue). `drop_with_audio=True` pose aussi le son lié sur la 1re piste audio déverrouillée.
- **Perf de l'aperçu (revue)** : l'essai ne copie plus toute la timeline. `Timeline.scoped_copy(pistes)` ne duplique que les plans des pistes concernées
  (+ pistes verrouillées + plans cités par un fondu enchaîné, pour que les règles inter-pistes restent justes) avec un clonage de plan rapide ; le verdict est mis en
  cache par (charge, piste, image, mode, révision du modèle) ; l'aimantation se fait par dichotomie. Mesuré (bac à sable) : 5 000 plans **~160 → ~4 ms** (p95),
  10 000 plans **~360 → ~6 ms** (p95). Un test compare le verdict « copie limitée » au verdict « copie complète » sur des centaines de dépôts aléatoires (verrous, modes,
  son lié, rush brut) : identiques.
- **Au dépôt : toujours revalidé** — jamais de cache, essai sur la copie COMPLÈTE, puis `Session.do` revalide encore (atomique). Test : aperçu en cache périmé + piste verrouillée → refus.
- Dépôt = UNE transaction (annulable d'un geste). Tests : `tests/test_bin_drag.py` (événements Qt réels de glisser/déposer inclus).

