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

## 3. Glisser depuis le chutier (PR chutier)
- Moteur : `place_subclip` (existant) et nouveau `place_rush(media, durée, piste, image, src_in, mode, …)` pour un rush brut — même code (`_place`),
  aucune entrée de chutier créée, plage bornée par la durée du rush.
- `ui/bin_view.py` : liste des plans du chutier, glisser (QDrag). `ui/dnd.py` : charge `{"sub": id}` ou `{"media", "length", "src_in", "kind"}` — que des références, jamais de chemin absolu ; charge étrangère/abîmée ignorée.
- `TimelineView.plan_drop` évalue un dépôt **sans rien modifier** : la validité vient du moteur (essai complet de la transaction sur une copie) →
  piste verrouillée, type de piste (vidéo/audio), plan verrouillé dessous, ripple refusé, fin du rush : raison affichée en rouge dans l'aperçu.
- Aperçu avant dépôt : fantôme vert (valide) / rouge (refusé, avec la raison), trait bleu = insertion. Aimantation sur bords, repères, tête de lecture.
- Modes : `drop_mode` (`overwrite` par défaut, ou `insert`), **Ctrl pendant le glisser inverse le mode**. L'insertion décale la suite de la piste visée seulement
  (pas des autres pistes ; le moteur refuse de désynchroniser une piste verrouillée). `drop_with_audio=True` pose aussi le son lié sur la 1re piste audio déverrouillée.
- Dépôt = UNE transaction (annulable d'un geste). Tests : `tests/test_bin_drag.py` (événements Qt réels de glisser/déposer inclus).

## 4. Formes d'onde asynchrones (PR formes d'onde)
- `ui/peak_service.py` : `PeakService.get(media)` **ne bloque jamais** (pyramide complète, partielle ou None) ; le calcul tourne dans 2 fils
  (ffmpeg décode en flux, une pyramide partielle est publiée toutes les ~10 s de média) → l'onde se remplit de gauche à droite.
- Priorité : `want([...])` (rushs visibles, les plus proches du centre de l'écran d'abord) passe devant la file.
- Cache disque : un `.npy` par rush, clé = chemin + taille + date + version, écriture atomique (`.tmp` + `os.replace`), cache abîmé → recalcul,
  rush modifié → nouvelle clé. Un rush sans audio est mémorisé. Le média source n'est jamais touché.
- Vue : `TimelineView(peak_service=...)` ; une tranche reçue invalide les images du rush et déclenche UN redessin regroupé (80 ms) ; une image d'onde n'est mise en
  cache que si elle est **définitive** (calcul terminé ou plan entièrement couvert) ; la partie non calculée = ligne médiane pointillée. Le chemin de dessin
  du pan/zoom ne change pas (mêmes caches par plan) → fluidité conservée.
- Arrêt propre : `close()` annule et tue ffmpeg (< 2 s). Tests : `tests/test_peak_service.py` (progressif, priorité, cache, corruption, annulation, fluidité du dessin
  pendant le calcul sur 480 plans / 8 pistes : médiane ~3 ms, p95 ~6 ms dans le bac à sable, rendu logiciel).
