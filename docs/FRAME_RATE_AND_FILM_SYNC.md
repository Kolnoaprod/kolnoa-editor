# Cadences mixtes, choix de timeline et synchronisation de films — proposition

Statut : spécification à valider, non implémentée. À planifier **après** la bêta Windows V0.1 et ses essais terrain. Ne pas modifier les PR #40–43 sans coordination.

## Objectif

Kolnoa Editor doit traiter aussi bien les reportages Sony A7 IV tournés principalement à 50 i/s (souvent exportés en 25 i/s pour une esthétique cinéma) que les films argentiques 24 i/s, sources vidéo 25 i/s, transferts LaserDisc et cadences NTSC 23,976/29,97/59,94.

## Trois cadences distinctes

1. **Cadence source** : fréquence exacte (rationnelle) et timebase native de chaque flux, sans arrondi.
2. **Cadence projet** : base temporelle de la timeline et cadence d'export proposée.
3. **Conversion** : politique explicite par source/clip ; elle ne doit jamais être confondue avec un changement de vitesse.

## Choix automatique de la cadence

- Analyser les cadences et durées cumulées des rushes (durée pondérée, pas seulement nombre de fichiers), en identifiant les plans ralentis et les sources atypiques si possible.
- Proposer une cadence dominante avec explication, sans la forcer.
- **Préférence Kolnoa** : lorsque le tournage est majoritairement en 50 i/s, proposer **25 i/s** comme cadence de projet par défaut, avec option 50 i/s.
- Proposer 24 pour scans film 24 ; 25 pour sources 25 ; conserver 24000/1001, 30000/1001, 60000/1001 lorsque pertinent.
- Permettre le verrouillage de la cadence par projet ; avertir avant modification après montage.

## Représentation temporelle

- FPS rationnels (numérateur/dénominateur) et timebase des médias, pas de flottants pour indexer les frames ou calculer des timecodes.
- Temps d'édition exprimés en ticks rationnels / fractions de seconde ; frames projet et frames source reliées par conversions exactes et politiques d'arrondi définies.
- VFR : respecter les timestamps PTS réels ; ne pas déduire la position à partir de `index / fps` pour les sources à cadence variable.
- Support timecode drop-frame/non-drop-frame pour les cadences concernées ; drop-frame modifie la numérotation, pas la vitesse de lecture.
- Sauvegarde/chargement et undo/redo doivent conserver les positions exactes lors de conversions et changements de cadence.

## Modes de conversion par clip

- **Conserver la durée** : rééchantillonner les images (drop/duplicate, blend ou interpolation optique selon choix) sans changer la durée réelle ; l'audio reste à sa vitesse.
- **Interpréter à la cadence du projet** : une image source devient une image projet, avec changement de vitesse et de durée, utile pour slow motion ou accélération de film ; traiter l'audio séparément.
- **Retiming manuel** : vitesse rationnelle/variable, rampes, correction de dérive ; prévoir interpolation et audio time-stretch.

Exemple 50 → 25 : en mode conserver la durée, une image sur deux en cadence régulière, sans ralentir le mouvement ; le flou de mouvement original ne change pas automatiquement.

## Synchronisation film 24 ↔ transfert vidéo 25

Cas : scan 35 mm 24 i/s et piste issue d'un LaserDisc/vidéo 25 i/s.
- Identifier la provenance : PAL speed-up 24→25 **possible mais non systématique** ; distinguer piste audio, vidéo et télé-cinéma.
- Si une source 24 a été accélérée à 25 sans répétition, sa durée est raccourcie par un facteur 24/25 ; restaurer la durée par 25/24 (≈ 4,167 % plus longue).
- Modes de synchro : offset fixe, ratio de vitesse constant, plusieurs points d'ancrage et correction de dérive progressive, avec prévisualisation des écarts.
- Repères : claps, pics audio, marqueurs manuels, corrélation audio, événements visuels et timecodes.
- Audio : choix time-stretch avec hauteur préservée ou correction de hauteur selon provenance, sans resampling aveugle.
- Ne jamais supposer qu'un média marqué « 25 fps » doit être ralenti ; les horodatages et la provenance font foi.
- Prévoir test d'une séquence longue avec repères au début, milieu et fin, pas seulement quelques secondes.

## Tests minimaux

- 50→25 conserve durée et synchronisation audio.
- 24→25 en préservant durée n'accélère pas le contenu ; interprétation à 25 accélère exactement selon le ratio attendu.
- 24000/1001 et 30000/1001 sans dérive après une heure.
- Cas 24/25 PAL speed-up : correction de durée 25/24 vérifiée sur une heure.
- Décalage fixe et dérive progressive corrigés sans saut aux points d'ancrage.
- VFR avec PTS irréguliers, exports et réouverture de projet.
- Les clips approuvés, modifications manuelles et historique sont protégés lors d'un changement de cadence.

## Coordination Companion

L'API Companion continue d'exposer `fps:{num,den}` et des timecodes/frame indexes non ambigus. L'UI Android affiche la cadence projet et les choix de conversion ; le moteur Windows reste l'autorité sur les calculs et rendus.
