# Fondus et transitions simples (spécification PR D)

**Confirmé par l'utilisateur** : des fondus et des transitions avec flash, « des choses simples », dans un
montage cinématographique haut de gamme, fluide, sans jump cuts agressifs. Pas d'effets compliqués.
Les durées par défaut et les règles d'emploi ci-dessous sont des **propositions à valider** (cf. issue #1) :
aucune durée n'est figée tant qu'elle n'a pas été vue à l'œil sur un vrai film.

## Catalogue volontairement court

| Transition | Rendu | Emploi proposé |
| --- | --- | --- |
| **Coupe** | aucune | par défaut entre plans d'une même action |
| **Fondu enchaîné** (`xfade fade`) | doux, invisible | cérémonie, émotion, discours, enchaînement de plans |
| **Fondu au noir** (`fadeblack`) | respiration | début et fin de film, changement de séquence |
| **Fondu au blanc** (`fadewhite`) | lumineux, onirique | souvenirs, ouverture, transition douce de séquence |
| **Flash** | pic de lumière de quelques images, coupe masquée | temps fort de la musique, entrée des mariés, teaser, soirée |
| **Fondu d'entrée / de sortie** d'un plan | déjà géré (`fade_in`/`fade_out`) | début et fin des plans, musique |

Hors catalogue tant que l'utilisateur ne le demande pas : glissés, whip pan, zoom, glitch, volets.
Le modèle est conçu pour les accueillir plus tard sans changer le schéma (type de transition = simple texte).

## Réalisation technique avec ffmpeg 6.1 (vérifié)

- Fondus : `xfade` (`fade`, `fadeblack`, `fadewhite`) + `acrossfade` pour que le son ne claque pas.
- Flash : un fondu au blanc très court avec pic d'exposition (`xfade fadewhite` sur ~4 images, ou surimpression
  d'une image blanche à opacité animée), centré sur la coupe pour la masquer. Aucun filtre exotique.

## Règles proposées (modifiables)

1. Cérémonie (Houppa, vœux, bénédictions) : coupe ou fondu enchaîné uniquement, **jamais de flash**.
2. Le flash est réservé aux temps forts : il tombe sur un repère `beat` (PR E) ; sans repère, pas de flash automatique.
3. Durées en **images entières**, jamais plus longues que la moitié du plus court des deux plans (sinon refus clair).
4. Cohérence : un seul style de transition « décorative » par séquence, durées constantes plutôt qu'aléatoires.
5. **Aperçu = export** : même chaîne de filtres pour le proxy et le rendu final (critère SSIM de la PR D).
6. Réversible et verrouillable comme le reste : `set_transition` / `remove_transition`, refusées sur un plan verrouillé.

## Valeurs de départ à tester (hypothèses, non confirmées)

Fondu enchaîné : environ 0,5 s (12 images à 25 i/s) ; fondu au noir : 0,8 à 1 s ; flash : 4 à 6 images au total.
À régler sur de vrais rushes puis à figer ici une fois validées.

## Questions ouvertes (une seule vraiment utile)

- Quelle durée te paraît juste pour le fondu enchaîné et pour le flash ? On teste les valeurs de départ sur un
  extrait et on ajuste à l'œil.

## Plan technique (PR D)

`Transition` dans le modèle (type texte, durée en images, paramètres) ; opérations `set_transition` /
`remove_transition` avec inverses ; générateur de filtres `xfade` + flash ; validation (durée max, plans voisins,
cadence) ; tests : durée totale exacte du film, parité aperçu/export, rollback ; catalogue exposé à l'assistant (PR E).
