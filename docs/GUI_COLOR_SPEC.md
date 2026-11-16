# Spec — interface (synchro, calibration) et couleur par angle

Statut : proposition à valider (aucun code). Maquette visuelle : voir la conversation Claude / ChatGPT.

## 1. Principes
- Timeline non destructive : tout réglage (synchro, couleur) est une opération enregistrée dans le projet ; les médias d'origine ne sont jamais écrits.
- Même cœur pour la ligne de commande et l'interface : l'interface appelle les fonctions de `sync/` et `sync/calibrate.py`, sans logique dupliquée.
- Un résultat « incertain » n'est jamais appliqué sans action explicite.

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

## 5. Découpage proposé des PR
1. PR C0 : modèle `color_grade` + ops + CLI (`color-set`, `color-lut`, `color-match`) + rendu ffmpeg + tests ; aucune interface.
2. PR C1 : `TaskRunner` + onglet Synchro / Calibration.
3. PR C2 : onglet Couleur (aperçu avant/après, curseurs, LUT, harmonisation).
4. PR D : rendu multicam, transitions, compensation de dérive (inchangé).

## 6. Questions ouvertes
- Boîte à outils graphique (Qt ou interface web locale) : à trancher avant C1.
- Détection fiable du profil Sony (S-Cinetone, S-Log3) via les métadonnées.
- Profil et espace colorimétrique des images de drone.
- Où stocker les LUT (projet ou bibliothèque partagée) et gestion de leur licence.
