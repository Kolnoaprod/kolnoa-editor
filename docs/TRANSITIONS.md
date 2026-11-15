# Fondus et transitions « style CapCut » pour l'événementiel (spécification PR D)

**Demande confirmée (utilisateur)** : des fondus et transitions sympas, façon CapCut, adaptés à l'événementiel,
dans un montage cinématographique haut de gamme, fluide, sans jump cuts agressifs.
Tout le reste de ce document (familles, durées, règles d'emploi) est une **proposition à valider** : aucune
durée n'est figée tant que ChatGPT / l'utilisateur ne l'ont pas confirmée (cf. issue #1).

## Ce qu'ffmpeg 6.1 sait déjà faire (vérifié dans le bac à sable)

`xfade` : `fade`, `fadeblack`, `fadewhite`, `fadegrays`, `dissolve`, `slideleft/right/up/down`,
`smoothleft/right/up/down`, `wipe*`, `circleopen/close`, `circlecrop`, `radial`, `zoomin`, `squeezeh/v`,
`pixelize`, `hlslice/hrslice`, `hlwind/hrwind`, `coverleft`, `revealleft`, `distance`… ; `acrossfade` pour le son.
Pas de pack « CapCut » tel quel : les effets de type *whip pan flou*, *flash/glow*, *glitch* sont **composés**
(xfade + flou directionnel `boxblur`/`gblur` + pic d'exposition `eq`/`curves` sur quelques images).

## Familles proposées (Transition = type + durée en images + paramètres, cf. `Clip.extra` puis champ dédié en PR D)

| Famille | Rendu | Emploi événementiel proposé |
| --- | --- | --- |
| **Fondu enchaîné** (`fade`/`dissolve`) | doux, invisible | cérémonie, émotion, discours ; par défaut |
| **Fondu au noir / au blanc** (`fadeblack`/`fadewhite`) | respiration, changement de séquence | début/fin de chapitre, ouverture et clap de fin |
| **Flash** (pic de lumière 3-5 images) | impact rythmé | coup de musique, entrée des mariés, drop |
| **Glissé / push** (`smooth*`, `slide*`) | mouvement dans le sens du plan | enchaînement de plans dynamiques, soirée |
| **Whip pan** (slide + flou directionnel) | transition « caméra » | danse, ambiance, passage cérémonie → soirée |
| **Zoom** (`zoomin` + recadrage) | pulsation | teaser, moments forts sur le temps de la musique |
| **Match cut / sur le mouvement** | coupe masquée | réservé à l'assistant (PR E), pas un effet |

## Règles proposées (modifiables)

1. **Pas de transition fantaisiste pendant la cérémonie** (Houppa, vœux, bénédictions) : coupe ou fondu seulement.
2. **Une seule famille « décorative » par séquence** pour garder une signature (pas de mélange sauvage).
3. **Calées sur la musique** : les transitions rythmées tombent sur les repères `beat` (PR E) ; sans repère, durées
   courtes et constantes plutôt qu'aléatoires.
4. **Durée en images entières** (aucune dérive) ; fondu audio assorti (`acrossfade`) pour que le son ne claque pas.
5. **Jamais plus longue que la moitié du plus court des deux plans** (sinon refus avec message clair).
6. **Aperçu = export** : la même chaîne de filtres sert à l'aperçu proxy et au rendu final (critère SSIM de la PR D).
7. Tout est **réversible et verrouillable** comme le reste : `set_transition` / `remove_transition` dans la timeline,
   une transition sur un plan verrouillé est refusée.

## Réglages à valider avec l'utilisateur (questions ouvertes)

- Quelles transitions CapCut aimes-tu **précisément** (captures ou noms) ? On les reproduira une par une.
- Durée par défaut du fondu enchaîné et du flash ? (proposition de départ à tester à l'œil sur un vrai film).
- Doit-on proposer des **presets par moment** (cérémonie / cocktail / soirée / teaser) ?
- Flou de mouvement des whip pans : subtil ou franc ?

## Plan technique (PR D)

`Transition` dans le modèle (type, durée en images, paramètres) ; opérations `set_transition`/`remove_transition`
avec inverses ; générateur de filtres `xfade` + composites (flash, whip) ; validation (durée max, plans voisins,
cadence) ; tests : durée totale exacte, parité aperçu/export, rollback ; catalogue exposé à l'assistant (PR E).
