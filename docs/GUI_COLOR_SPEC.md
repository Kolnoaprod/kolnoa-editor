# Spec — interface prompt-first : montage, synchro, calibration, couleur par angle

Statut : proposition à valider (aucun code). Maquette visuelle : voir la conversation Claude / ChatGPT.

## 1. Principes
- Timeline non destructive : tout réglage (synchro, couleur) est une opération enregistrée dans le projet ; les médias d'origine ne sont jamais écrits.
- Même cœur pour la ligne de commande et l'interface : l'interface appelle les fonctions de `sync/` et `sync/calibrate.py`, sans logique dupliquée.
- Un résultat « incertain » n'est jamais appliqué sans action explicite.

## 1 bis. Montage prompt-first avec retouches ancrées
- Par défaut tout se fait par prompt ; l'éditeur sert à regarder et à retoucher complètement à la main.
- **Retouche ancrée** : l'utilisateur avance dans l'aperçu, s'arrête, écrit un prompt (« ici, passe sur CAM 2 », « raccourcis ce silence ») ; il est rattaché au plan source et au temps (`comments.py`), pas au temps du rendu.
- **File de retouches** : plusieurs retouches peuvent être posées à la suite ; elles s'exécutent ensuite en lot (bouton « Appliquer les retouches »), chacune restant `open`, `done` ou `orphan`.
- **Progression visible** : dès qu'un prompt ou un lot est lancé, une barre indique l'étape (lecture, analyse, calcul des opérations, vérification des verrous), la retouche en cours, une estimation du temps et un bouton Annuler ; elle repose sur le `TaskRunner` (§2).
- **Diff avant validation** : le lot produit une liste d'opérations lisible (durée avant/après, verrous respectés) ; validation totale, partielle ou refus ; chaque validation crée une version comparable.
- **Parité** : toute action manuelle est une opération ; toute opération de l'IA est modifiable à la main ; ce qui est posé à la main devient un verrou que l'IA ne défait pas au prompt suivant.
- **Éditeur manuel, par étapes** : lecture et sélection, puis coupes / déplacements / changement d'angle, puis audio, vitesse, transitions, couleur.
- **Hors ligne** : les opérations manuelles marchent sans clé API ; les fournisseurs d'IA sont optionnels.

## 1 ter. Flux de travail et exigences de la timeline manuelle
### Flux
1. **Synchro** (onglet Synchro) puis **vérification** : vue de contrôle (formes d'onde superposées, comparaison image par image, `multicam-nudge`) ; rien n'est utilisable tant qu'un angle « incertain » n'est pas accepté.
2. **Multicam**, au choix : à la main (touches numériques) ou par prompt (l'IA pose les coupes d'angle sur la timeline) ; dans les deux cas le résultat est visible et retouchable sur la timeline.

### Multicam à la main
- Touches `1`…`9` : coupe vers l'angle correspondant à la position de la tête de lecture, en lecture ou à l'arrêt (opération équivalente à `multicam-at`).
- L'audio est une piste à part, indépendante des angles ; changer d'angle ne change jamais le son.

### Audio
- Pistes audio séparées ; mono → dual mono (ou mono vers stéréo), niveaux par clip et par piste, fondus d'entrée/sortie, fondu enchaîné audio ; rendu via ffmpeg (`pan`, `volume`, `afade`, `acrossfade`).
- Mixage de la musique et du son de l'enregistreur sur des pistes distinctes.

### Choix de la piste audio
- Pour chaque groupe multicam, le logiciel **propose** la meilleure source audio d'après des mesures sur les pistes synchronisées : niveau et plage dynamique, bruit de fond, saturation (clipping), silences, bande passante, présence de parole ; le score est indicatif et les mesures sont affichées.
- L'utilisateur peut **imposer** sa piste préférée (par groupe, par scène ou pour tout le projet) ; ce choix devient un verrou que les prompts respectent.
- Les pistes des caméras servent de repère (« guide ») quand elles sont exploitables ; sinon elles sont ignorées pour le son.
- Le traitement suit la source retenue : normalisation du niveau, mono → dual mono, fondus ; réduction de bruit seulement si demandée.

### Outil lame de rasoir et raccourcissement
- Rasoir : coupe l'image **et** le son liés au même instant (clips liés par défaut) ; option pour couper une seule piste, ou toutes les pistes.
- Raccourcir un passage : retirer la portion entre deux coupes avec ou sans ripple, ou ajuster les bords (trim) ; l'image et le son restent synchronisés tant que les clips sont liés.
- Même opération par prompt (« raccourcis ce passage de 30 s sans couper les paroles »), avec diff avant validation.

### Chutier et plans de coupe
- Chutier des rushes avec lecture, marques `I` / `O`, création d'un sous-clip ; glisser-déposer sur la timeline.
- Les sous-clips pointent vers le rush d'origine (jamais copié ni modifié).

### Placement et indépendance des pistes
- Par défaut, déplacer ou poser un plan sur une autre piste que la piste multicam ne décale rien, ni avant ni après.
- Aimantation (magnet) activable : bords des clips, coupes, marqueurs, tête de lecture.
- Modes d'édition explicites : **écraser** (par défaut), **insérer** (décale la suite sur la piste visée), ripple activable et désactivable ; verrou par piste.
- Compensation : rallonger ou raccourcir le plan précédent ou suivant (poignées, limitées par la durée du rush source), pour combler ou absorber un plan intercalé.
- Un clip posé à la main devient un verrou que les prompts respectent.

## 2. Couche « tâches en arrière-plan » (commune)
- `TaskRunner` : exécute une tâche longue dans un processus ou un thread séparé, avec progression (`current/total`, libellé), annulation coopérative et journal.
- Les analyses de calibration et de synchro émettent des événements de progression (paire, fenêtre) ; l'interface ne fait que les afficher.
- Tests sur le `TaskRunner` (progression, annulation, erreur), pas sur l'affichage.

## 3. Onglet Synchro / Calibration
- Entrées : dossier des rushes, scénario (MAIRIE, préparatifs, libre), indices (horodatage, visuel expérimental).
- Boutons : Plan (dry-run) puis Analyser. Garde-fous repris de `sync-calibrate` : lecture seule, instantané taille/mtime avant/après, rapport refusé dans les rushes.
- Résultats : tableau par angle (décalage, dérive, statut, confiance indicative, notes), angles sans audio signalés, actions Accepter / Régler à la main, aperçu des pistes alignées.
- Rapports (md/json/csv) écrits dans un dossier choisi, hors dépôt public.

## 4. Couleur par angle (nouveau)
### Modèle (opération `color_grade`, par angle ou par clip)
- `input_profile` : `auto` | `s-cinetone` | `s-log3` | `rec709` | autre (détection à partir des métadonnées si possible, sinon choix manuel).
- `lut` : référence à un fichier `.cube` (chemin + empreinte), `lut_strength` 0–100 %.
- `adjust` : exposition (IL), température (K), teinte, contraste, saturation.
- `scope` : clip | angle | groupe multicam.
- `reference_angle` optionnel : angle servant de cible pour l'harmonisation.

### Harmonisation automatique (« faire correspondre à la référence »)
- Mesure sur des passages simultanés (grâce à la synchro) : luminance médiane, balance moyenne dans les tons moyens, écart de teinte.
- Calcule des valeurs de départ pour exposition / température / teinte ; l'utilisateur retouche à la main. Résultat jamais appliqué sans validation.

### Rendu
- Chaîne ffmpeg par angle : conversion de profil, `lut3d` (intensité par mélange), puis réglages (`eq`, `colorbalance`/`colortemperature`). L'aperçu utilise la même chaîne en basse résolution.
- Ordre fixe et documenté ; une image de test par profil pour vérifier qu'aperçu et rendu concordent.

### Hors périmètre initial
- Étalonnage par masques, courbes, suivi, scopes avancés.

## 5. Découpage proposé des PR (à valider ; modifie l'ordre D/E d'origine)
1. PR C0 : modèle `color_grade` + ops + CLI + rendu ffmpeg + tests (sans interface).
2. PR C1 : `TaskRunner` + onglet Synchro / Calibration.
3. PR C2 : onglet Montage (aperçu, timeline en lecture et sélection, barre de prompt, retouches ancrées, file et diff).
4. PR E1 : prompts → opérations (montage de base, retouches par lot) ; les 6 prompts d'acceptation de l'issue #2 servent de tests de bout en bout.
5. PR C3 : édition manuelle de la timeline (coupes, déplacements, angles), puis audio / vitesse / transitions.
6. PR C4 : onglet Couleur.
7. PR D : rendu multicam, transitions, compensation de dérive.

## 6. Questions ouvertes
- Boîte à outils graphique (Qt ou interface web locale) : à trancher avant C1.
- Détection fiable du profil Sony (S-Cinetone, S-Log3) via les métadonnées.
- Profil et espace colorimétrique des images de drone.
- Qualité des analyses locales (paroles, scènes, musique) : à mesurer sur de vrais rushes avant de promettre un montage automatique.
- Où stocker les LUT (projet ou bibliothèque partagée) et gestion de leur licence.
