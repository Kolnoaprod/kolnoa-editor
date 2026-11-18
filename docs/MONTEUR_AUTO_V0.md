# Monteur automatique v0 — workflow de bout en bout (sans interface)

Branche `feat/auto-editor-v0`, empilée sur la PR #33 (provenance / garde de portée / sélection). Brouillon, non fusionnée.
Budget 0 € : analyse **locale** (ffmpeg `silencedetect`), aucune API, aucun service.

## Ce que ça fait
`ProjectController` (`kolnoa_editor/app/`) enchaîne, sur UNE timeline et UNE `Session` :

1. **ouvrir / créer** un projet (dossier de rushes en lecture seule ; données dans le dossier de données) ;
2. **importer** : scanner + ffprobe existants → rushes vidéo utilisables ;
3. **« Monte-moi cette séquence [en N min] »** : analyse locale (parole/silence) → `plan_sequence` (déterministe : ordre chronologique,
   plans bornés par `shot_min/shot_max`, durée visée par sélection des meilleurs scores, plan d'ambiance pour les rushes sans parole)
   → `generate_first_edit` : groupe `auto`, **une transaction par section** (dossier d'origine), son lié, annulable d'un geste ;
4. **consignes ciblées** sur la sélection partagée : *raccourcis [de N s | à N s | de moitié | de N %]*, *accélère le rythme [×N]*,
   *remplace par un plan de coupe* (posé sur V2, au-dessus de V1) — ne touchent QUE la zone choisie (les suites sont seulement décalées) ;
5. **retouche manuelle** par les mêmes primitives (la souris passera par `Session.do`) ;
6. **enregistrer / rouvrir** : journal + provenance reconstruits à l'identique.

## Garanties (testées)
- Un montage automatique = un `undo_group` ; chaque section reste un pas annulable.
- Une consigne ne modifie jamais un plan **validé** (refus explicite) ; une retouche manuelle voisine est conservée et reste `manuel`.
- Sélection périmée (timeline changée depuis) → refus.
- Fichiers réels : test avec vidéos générées par ffmpeg (son/silence/son) → moments détectés, montage, réouverture identique.

## Limites assumées de la v0
Pas de compréhension sémantique (personnes, actions) — étude locale gratuite plus tard. Le multicam automatique (choix d'angle) n'est pas
encore branché ; la musique et le rendu MP4 sont dans le lot J1. Les seuils (silence −35 dB, plans 2–6 s) sont à calibrer sur les
rushes du réalisateur : c'est la mesure de qualité attendue du premier jalon.
