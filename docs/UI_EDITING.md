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
- **Alt + glisser = ripple** (arbitrage de revue) : `ripple_limits` / `ripple_edge` (moteur). La suite de la piste (et des pistes des plans liés) suit ; pas de limite de
  voisin ; restent durée ≥ 1 image + fondus, poignées du rush, liés, verrous. Aperçu : étiquette `RIPPLE`, fantômes orange des plans qui bougent, et **validation du moteur**
  pendant le geste (essai sur copie, une fois par valeur de delta ; erreur affichée en rouge) puis revalidation à l'application (`Session.do` atomique). Alt peut être pressé/relâché en cours de geste.
- Tests : `tests/test_trim.py` (moteur, ripple compris) et `tests/test_ui_timeline.py` (geste complet, limite, Échap, poignées source, Alt+glisser + annulation).
