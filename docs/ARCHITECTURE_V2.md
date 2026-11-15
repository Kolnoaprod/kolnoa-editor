# Architecture v2 — station de montage hybride

Réponse aux issues [#1](https://github.com/Kolnoaprod/kolnoa-editor/issues/1) et [#2](https://github.com/Kolnoaprod/kolnoa-editor/issues/2). Ce document est une **proposition à valider** avant les PR B à E ; la PR A (modèle de timeline) en applique la partie « modèle ». Le socle actuel (CLI, listes de décisions, rendu) est conservé : rien n'est supprimé.

## 1. Principe directeur

Un seul **état de projet** (la timeline), modifié uniquement par des **opérations** validées et réversibles. La souris, le clavier, la CLI et l'IA produisent les mêmes opérations. Il n'existe donc pas de « mode IA » séparé qui pourrait contredire le travail manuel.

```
 souris / clavier ─┐
 CLI ──────────────┼─▶ Opérations ─▶ Timeline ─▶ plan de rendu ─▶ ffmpeg ─▶ preview (proxy) / export final
 assistant IA ─────┘   (undo/redo,    (état        (le même pour          (même graphe de filtres,
  (propose, ne         verrous,        canonique,   preview et export)     résolution différente)
   décide pas)         diff)           versionné)
```

Règles non négociables :
1. Les rushes originaux ne sont jamais écrits. Aucun média n'est envoyé à une API sans accord explicite.
2. Une correction locale ne reconstruit jamais le film : elle applique des opérations sur l'existant.
3. L'IA n'écrit pas de JSON libre : elle renvoie une liste d'opérations typées, validées par schéma, appliquées sur une **copie**, présentées en diff ; l'utilisateur accepte ou refuse en un bloc (un seul pas d'undo).
4. Les plans et séquences **verrouillés** ne sont jamais modifiés par une opération, quel que soit son auteur.

## 2. Modèle de timeline canonique

Fichier `project.json` (écriture atomique) + journal d'opérations `history.jsonl` + versions nommées. Schéma versionné avec migrations testées (`schema: 1, 2, …`).

**Temps.** Positions et durées de timeline en **images entières** à la cadence du projet (aucune dérive flottante, raccords exacts à l'image, comme vérifié en PR 1). Temps source en secondes (6 décimales). Les conversions passent par un unique module `timebase`.

| Entité | Contenu |
| --- | --- |
| `Media` | id stable, chemin relatif, appareil, empreinte, infos ffprobe, état du proxy, espace colorimétrique d'entrée, décalage d'horloge |
| `Track` | id, type vidéo/audio, rôle (principal, ambiance, voix, musique, effets), verrou, muet, solo, gain |
| `Clip` | id stable, piste, référence `Media` **ou** `MulticamGroup`, début/durée (images), `src_in`, vitesse, transitions entrante/sortante, pile d'effets, étalonnage, gain et fondus audio, verrou, étiquette, `parent` (filiation) |
| `MulticamGroup` | angles `{media, décalage, dérive, confiance, méthode, statut}`, source audio dédiée, axe de temps commun |
| `Transition` | type (fondu, dissolve, fondu au noir, volet…), durée, paramètres |
| `Effect` | type, paramètres, image clés |
| `Grade` | exposition, balance des blancs, contraste, HL/ombres, saturation, courbes, LUT (référence), N&B et vignettage en option |
| `Marker` | image, nature (temps fort, phrase, repère de synchro, chapitre, **zone à ne pas couper**), texte |
| `Comment` | rattaché à `Clip` + temps source (comportement actuel conservé) |

**Compatibilité.** `Timeline.from_decision_list()` importe une liste de décisions existante (1 piste vidéo) ; `to_decision_list()` exporte les montages simples. La CLI et le rendu actuels continuent donc de fonctionner. Les ids ne sont jamais réutilisés (`retired`, déjà en place depuis la PR 1).

## 3. Opérations réversibles

Patron « commande » : `apply()` retourne l'opération inverse ; un `Transaction` regroupe plusieurs opérations en un seul pas d'undo. Journal persistant (undo possible après redémarrage), autosave, instantanés nommés.

Opérations de base : `add_track`, `add_clip`, `remove_clip`, `move_clip`, `trim_clip` (bord in/out), `split_clip`, `ripple_delete`, `set_speed`, `set_transition`, `set_effect`, `set_grade`, `set_gain`, `add_marker`, `lock`, `create_multicam`, `switch_angle` (changer d'angle sans toucher à l'audio), `set_sync`.

Chaque opération déclare : ses préconditions, ce qu'elle verrouille/respecte, et son diff lisible (« + s014 plan de la Houppa 4,2 s », « ~ s003 raccourci de 1,1 s »).

## 4. Synchronisation hybride (PR B)

| Méthode | Statut | Détail |
| --- | --- | --- |
| Métadonnées (noms DJI/Canon, `creation_time`, date de fichier) | **réalisable maintenant** | placement initial ; décalages d'horloge par appareil à calibrer ; fuseaux et prises discontinues gérés |
| Audio par corrélation (caméra ↔ caméra, caméra ↔ enregistreur) | **réalisable maintenant** | corrélation généralisée (type GCC-PHAT) ; confiance = rapport pic / lobes secondaires |
| Dérive des prises longues | **réalisable maintenant** | corrélation en plusieurs fenêtres (début, milieu, fin) puis régression linéaire (ppm) |
| Alignement manuel (2 repères) | **réalisable maintenant** | décalage + dérive, puis verrouillage ; vue côte à côte, image par image |
| Visuelle par événements globaux (flash, extinction/allumage, éclairs) | **réalisable avec prudence** | corrélation de séries temporelles de luminance entre caméras qui voient le même changement de lumière |
| Visuelle drone aérien ↔ caméra au sol | **expérimental** | échelles et angles très différents : proposition de candidats (événements, mouvements) avec score, jamais une promesse |

Résultat : `SyncResult{méthode, décalage, dérive, confiance 0–1, preuves, statut}` avec statuts `auto`, `suggéré`, `manuel`, `verrouillé`, **`non fiable`**. Sous le seuil de confiance, Kolnoa affiche « non fiable » et demande **deux repères manuels** au lieu d'inventer. Le drone n'a pas de son : son placement par défaut reste son horodatage + décalage d'horloge calibré une fois par mariage.

## 5. Multicam intelligent

- Groupe d'angles sur un axe de temps commun ; l'audio est une source distincte de l'angle vidéo (enregistreur principal conservé quand on change d'angle).
- Changement d'angle = `switch_angle` sur des plages de la timeline ; vues 2/4/8 angles et touches 1–8 en PR C.
- Montage multicam IA (PR E) : scores par angle et par fenêtre (netteté, mouvement, visages sans identification, exposition), coupes calées sur les temps forts et phrases musicales, durée minimale par angle, pas de jump cut sec par défaut. Chaque score est **explicable** et chaque plan peut être exclu ou verrouillé.

## 6. Preview et rendu : un seul plan de rendu

Le piège à éviter : une preview qui ne ressemble pas à l'export. Solution : la timeline est *aplatie* en **intervalles** (entre deux points d'édition) ; chaque intervalle a un graphe de filtres ffmpeg construit par le **même code** pour la preview (proxy, demi-résolution) et pour l'export (originaux, pleine résolution).

- Les intervalles encodés sont mis en cache (c'est le cache de segments de la PR 1, généralisé) : une retouche ne ré-encode que les intervalles touchés → preview proche du temps réel pour des coupes, quelques secondes pour des effets.
- Lecture fluide : le lecteur joue les proxys directement pour naviguer, et le cache de preview pour la lecture avec effets/transitions/mixage.
- **Parité vérifiée par tests** : rendu « preview » vs « export » réduit à la même taille, comparaison d'image (SSIM) et de loudness sur des projets synthétiques.
- Transitions : `xfade` (fondu, dissolve, fondu au noir, volets) et `acrossfade` pour l'audio. Transitions de mouvement ou à masque : **expérimental**, hors socle.
- Export 1080p/4K, 25/50 i/s, NVENC si disponible (sinon x264), contrôle qualité, reprise (déjà en PR 1). Interchange EDL/FCPXML : à terme, au mieux pour les montages à une piste vidéo.

## 7. Étalonnage et LUT, non destructifs

Chaîne par plan : **transformation d'entrée** (log → Rec.709 via une LUT d'entrée choisie selon l'espace colorimétrique du média) → **étalonnage** (réglages et/ou LUT créative) → sortie Rec.709. On ne confond pas LOG et LUT : l'espace d'entrée est une propriété du média (`slog3`, `dlog`, `rec709`…), la LUT est un outil. Réglages stockés dans le projet, jamais cuits dans les fichiers. Harmonisation Sony / Osmo / drone : préréglages par appareil (réalisable), puis correspondance automatique par statistiques (balance, luminance) proposée avec aperçu avant/après (**expérimental**). N&B et vignettage subtil : options.

## 8. Audio et musique

- Pistes par rôle : son direct, voix/discours, ambiance, musique, effets.
- Marqueurs de temps forts et de phrases (analyse locale : flux spectral + tempo ; `librosa` en option). « Rythmer » ajuste les points de coupe et les durées de transition sur ces marqueurs, **sans jamais traverser une zone « à ne pas couper »** (discours, vœux) détectée par transcription (faster-whisper) ou posée à la main.
- Ducking de la musique sous la voix (`sidechaincompress`), normalisation (`loudnorm`), limiteur de crête (`alimiter`), fondus.

## 9. Interface (PR C)

**Recommandation : PySide6 (Qt) + libmpv.** Un seul langage avec le cœur Python, lecture image par image native, dessin personnalisé de la timeline (`QGraphicsView`), empaquetage Windows possible. Alternative envisagée : interface web locale (Tauri/Electron) — plus de liberté graphique, mais synchroniser la lecture vidéo avec une timeline web est plus fragile et ajoute un second langage. **À valider.** Contenu : chutier par appareil/jour, timeline multipiste (glisser-déposer, trim, split, ripple, snapping, zoom), lecture J/K/L, vue multicam, alignement côte à côte pour la synchro, undo/redo, autosave, commentaires sur image, panneau « Demander à l'assistant » avec diff.

## 10. Assistant IA (PR E)

- Fournisseurs derrière une interface (`Claude`, `OpenAI`) ; **optionnels** ; clés dans le stockage sécurisé de Windows ; coût estimé affiché avant l'envoi.
- Données minimales : résumés de la timeline et des analyses (texte), jamais de vidéo par défaut ; chaque envoi listé et soumis à consentement.
- **Mode hors ligne** : « Monte-moi ce film » dispose d'un montage heuristique local (séquences chronologiques, scores locaux, musique) ; l'IA l'améliore, elle n'est pas indispensable.
- Les 6 prompts d'acceptation de l'issue #2 sont des **tests de bout en bout** de la PR E (voir §12).

## 11. Réalisable maintenant / expérimental

| Réalisable maintenant | Réalisable avec prudence | Expérimental (annoncé comme tel) |
| --- | --- | --- |
| Modèle, opérations, undo/redo, migrations | Synchro visuelle par flash / changement de lumière | Synchro visuelle drone aérien ↔ sol |
| Synchro par métadonnées, audio, dérive, manuelle | Correspondance colorimétrique automatique | Transitions de mouvement / à masque |
| Multicam par groupes, changement d'angle | Stabilisation et recadrage 4K → 1080 (prudents) | Ralentis par interpolation de mouvement |
| Transitions fondu/dissolve/noir, LUT, grade | Détection de moments forts (scores explicables) | Jugement « émotion » des plans |
| Pistes audio, ducking, loudness, limiteur | Ralenti 50 → 25 i/s (images conservées, pas d'invention) | |

## 12. Plan de PR, critères de validation et risques

| PR | Contenu | Critères de validation | Risques / dépendances |
| --- | --- | --- | --- |
| **#3 (PR 1)** | Fiabilité du socle | 58 tests ; bugs prouvés corrigés | Drive en streaming, NVENC non testé |
| **A** | Modèle de timeline, opérations réversibles, migrations, import/export des listes de décisions, rendu compatible | opérations ⇄ inverses (propriétés), journal rejouable, import d'anciennes listes, rendu identique au rendu actuel sur les mêmes données | Choix du temps en images entières ; schéma à geler avec prudence |
| **B** | Groupes multicam, synchro audio + dérive + métadonnées + manuelle, score de confiance, clips discontinus, drone muet | décalage retrouvé sur signaux synthétiques (±1 image), dérive en ppm, « non fiable » levé sous le seuil, aucun décalage inventé | numpy (+ éventuellement scipy) ; vrais rushes pour calibrer les seuils |
| **C** | UI : timeline, lecteur proxy, vue multicam, alignement visuel côte à côte, undo/redo, autosave | scénarios manuels + tests d'interface automatisés sur le modèle ; captures dans la PR | PySide6 + libmpv sur le Shadow PC (streaming) ; performance de la timeline |
| **D** | Effets, LUT, grade, transitions, musique, mixage, parité preview/export | SSIM preview/export au-dessus d'un seuil ; loudness conforme ; LUT d'entrée correcte sur charte synthétique | LUT S-Log3 de l'utilisateur ; `xfade` selon la version d'ffmpeg |
| **E** | Opérations par prompt, diff avant validation, montage automatique, « Rythmer », fournisseurs optionnels | les 6 prompts d'acceptation produisent des opérations valides ; verrous respectés ; hors ligne fonctionnel | Clés API, coût, qualité des analyses locales (OpenCV, faster-whisper) |

**Prompts d'acceptation ↔ PR.** #1 teaser sur musique : A + D + E. #2 priorités de caméras, son de l'enregistreur : A + B + E. #3 synchro du drone avec niveau de confiance : B (affichage en C). #4 raccourcir la cérémonie sans couper les paroles : A + marqueurs de §8 + E. #5 harmoniser les couleurs, fondu, ralenti : D + E. #6 rythmer la danse sans jump cuts : D + E.

## 13. Décisions à valider

1. Temps de timeline en images entières, temps source en secondes.
2. PySide6 + libmpv pour l'interface (contre une interface web).
3. Plan de rendu unique pour preview et export, avec cache d'intervalles.
4. Statut `non fiable` + deux repères manuels plutôt qu'un alignement visuel « devinette ».
5. Données Kolnoa hors du Drive (`KOLNOA_DATA_DIR`) par défaut à terme.
