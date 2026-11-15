# Synchronisation multicam (PR B)

Code : `src/kolnoa_editor/sync/` + groupes multicam dans `src/kolnoa_editor/timeline/`.
Métriques reproductibles : `python scripts/bench_sync.py` → `docs/SYNC_METRICS.md`.
Cadre : issue #2 (critères PR B) et `docs/STYLE_GUIDE.md` (section Multicam et drone, PR #8).

## Principe : ne jamais inventer une synchro

Chaque angle porte un **état** et une **confiance** ; seuls `reference`, `ok` et `manual` sont *utilisables*
(`Angle.usable`). Un plan multicam refuse un angle non utilisable.

| État | Sens | Utilisable |
| --- | --- | --- |
| `reference` | angle maître (audio de l'enregistreur) : définit l'axe du groupe | oui |
| `ok` | audio : pics nets, fenêtres concordantes (confiance ≥ seuil) | oui |
| `incertain` | mesure plausible mais fragile, ou **proposition visuelle** | non, validation humaine requise |
| `discontinu` | le clip saute (plusieurs décalages) : à découper en segments | non |
| `impossible` | aucune correspondance démontrée : l'ancien décalage n'est jamais écrasé ni réutilisé | non |
| `manual` | réglé ou validé par l'humain (repères, image par image, `multicam-accept`) | oui |
| `unsynced` | jamais synchronisé (ex. drone) | non |

Axe commun : `t_groupe = offset + t_angle × (1 + dérive_ppm × 1e-6)`. Le **son du plan multicam vient toujours du
maître** (`audio="group"`) : changer d'angle (`switch_angle`) conserve le même instant de groupe et ne touche pas
l'audio. L'export en liste de décisions refuse les plans multicam tant que le rendu multicam n'existe pas (PR D).

## Synchro audio (`sync/audio.py`)

1. **Recherche grossière** : enveloppes d'énergie (100 Hz, tendance retirée), corrélation normalisée sur tout le
   maître (ou autour d'un indice métadonnées). Le fichier long n'est jamais chargé entier (calcul par blocs).
2. **Affinage** : jusqu'à 12 fenêtres de 20 s réparties dans le clip, corrélation de phase (GCC-PHAT, bande 150–3500 Hz)
   dans ±60 s de l'attendu → détecte aussi les sauts. Fenêtres silencieuses ignorées.
3. **Droite décalage/temps** (Theil–Sen puis moindres carrés sur les inliers) → décalage + **dérive en ppm** ;
   2ᵉ passe qui *recompense* la dérive dans chaque fenêtre (sinon le pic s'étale : 20 ms de glissement à 1000 ppm).
4. **Discontinuités** : saut d'offset entre fenêtres voisines > `jump_tol_s` → plusieurs segments, état `discontinu`,
   `split_discontinuous` crée une entrée d'angle par segment (`A7`, `A7.2`…) avec sa plage de validité.
5. **Confiance** = moyenne géométrique de (netteté des pics, couverture, accord entre fenêtres). Seuils
   `ok_confidence`, `uncertain_confidence`, `min_psr`… dans `SyncConfig` (JSON via `--config`) : **valeurs initiales,
   pas des vérités**.
6. **Ambiguïté** : si un second alignement (à plus de `coarse_ambiguity_exclusion_s` du premier) atteint
   `coarse_ambiguity_ratio` × la hauteur du premier (même chanson jouée deux fois, boucle), un résultat « ok » est rétrogradé
   en `incertain` avec la note « alignement ambigu ». Un indice de métadonnées restreint la recherche et lève le doute,
   sans jamais se substituer à la mesure.

### Le score de confiance est une heuristique, pas une probabilité

La confiance (0–1) est un **score de classement** : moyenne géométrique de trois termes sans unité commune. Elle sert à
trier et à fixer des seuils d'état ; elle **ne signifie pas** « x % de chances que ce soit juste » et ne doit pas être
affichée comme telle. Les signaux synthétiques ne démontrent aucune calibration. Seule la comparaison avec des repères
vérifiés à l'image sur de vrais rushes (`kolnoa sync-calibrate`, section Calibration) permet de dire quel seuil donne
quel taux d'erreur ; ces taux, mesurés, remplaceront le mot « confiance » dans l'interface.

### Conventions de temps, conversions inverses et validité

`t_groupe = offset + t_angle × (1 + dérive_ppm × 1e-6)` pour `t_angle` dans la plage de validité du segment
(`Angle.range`, en secondes de l'angle, **bornes incluses** `[début, fin]`). Inverse :
`Angle.to_angle(t_groupe) = (t_groupe − offset) / (1 + dérive×1e-6)` (aller-retour exact à 1e-9 s). Hors de la plage,
`source_times_at` renvoie `None` (pas d'extrapolation silencieuse) ; un instant exactement à la frontière de deux segments
d'un même fichier est valide pour **les deux** (à l'appelant de choisir : le rendu prendra le segment qui commence).
Un clip sans rien de commun avec le maître est `impossible` ; un clip qui déborde du maître (en partie hors
enregistrement) n'est jamais `ok` : la partie sans correspondance est signalée en note.

### Cas difficiles testés (`tests/test_sync_robustness.py`)

Boucle strictement périodique → `impossible`/`incertain`, jamais « ok » à un faux instant ; applaudissements
partagés → ok, sans rapport → pas « ok » ; écho long (RT ≈ 1 s) → décalage conservé à moins d'une image ; même chanson
deux fois → `incertain` + note. Limite connue : **une musique identique jouée à deux instants et enregistrée en entier
par l'angle reste indécidable sans indice** ; c'est précisément le rôle de la validation humaine.

Limites assumées : sans structure d'énergie (bruit stationnaire pur) la recherche grossière échoue → « impossible » ;
un saut plus grand que `wide_search_s` apparaît comme « fenêtres sans correspondance » (noté) ; la dérive n'est pas
mesurée sur un clip de moins de 60 s (dit explicitement) ; la vidéo n'est pas étirée (la dérive sera compensée sur
l'audio au rendu, PR D).

## Horloges distinctes et métadonnées

Plusieurs fichiers d'une même caméra = autant d'angles, chacun synchronisé séparément. Les horodatages de fichiers
(`start`) ne servent **que d'indice** (`metadata_hint`, tolérance `clock_tolerance_s`) pour restreindre la recherche ;
jamais appliqués comme synchro.

## Drone muet : synchro visuelle assistée, EXPÉRIMENTALE (`sync/visual.py`)

Séries de luminosité (miniatures 32×18, 10 i/s) → événements (flash, lumières de piste, soleil/nuage) → corrélation →
**candidats classés** avec confiance plafonnée (`visual_max_confidence` 0,6, jamais « ok »), nombre d'événements
concordants et **score heuristique de risque de coïncidence** (approximation de Poisson corrigée du nombre de décalages testés ; ce n'est pas une probabilité statistiquement validée). États :
`proposition`, `faible` (à vérifier), `ambigu` (événements répétitifs), `impossible` (pas assez d'événements : on
propose un alignement manuel). Rien n'est appliqué automatiquement : `multicam-sync --accept-visual N` pose la
proposition en `incertain`, l'humain la valide (`multicam-accept`) ou la règle image par image. Une vue aérienne et
un plan serré peuvent n'avoir aucun événement commun : le module le dit.

## Alignement manuel

- **Repères** : `multicam-align g001 DRONE --point T_ANGLE T_GROUPE` (1 repère = décalage ; 2 repères ≥ 30 s d'écart =
  décalage + dérive ; repères trop proches ou dérive invraisemblable refusés), `--snap` arrondit à l'image.
- **Image par image** : `multicam-nudge g001 DRONE +1` (cadence du projet), réversible.
- **Verrou** : `multicam-lock` protège l'angle de la synchro automatique et de tout réglage.
- **Côte à côte** : `multicam-at g001 T` donne l'instant source de chaque angle pour ouvrir les vues à aligner
  (interface graphique : PR C).

## CLI

`multicam-create`, `multicam-sync [--dry-run] [--config] [--accept-visual N]`, `multicam-align`, `multicam-nudge`,
`multicam-lock`, `multicam-accept`, `multicam-at` ; tout passe par la timeline : annulable (`timeline-undo`), journalisé.

Mesure sur de vrais rushes : `kolnoa sync-calibrate`, voir [SYNC_CALIBRATION.md](SYNC_CALIBRATION.md) (constats réels, réglages modifiés, ce qui reste non mesuré).

## Rushes nécessaires pour calibrer (à fournir, rien ne bloque le développement)

1. **2–3 caméras avec audio sur un même moment** (A7 IV, Osmo, Canon…) + l'**enregistreur WAV** de la même scène :
   cérémonie (parole) et soirée (musique forte) — pour fixer `min_psr`, `good_psr`, `ok_confidence`.
2. **Un même événement filmé par chaque caméra** avec un repère net (claps de mains, flash photo) et son temps noté.
3. **Prises discontinues** : une caméra arrêtée/relancée en cours d'enregistrement, et un fichier long coupé en parties.
4. **Une prise longue (≥ 20 min)** par appareil pour mesurer la **dérive réelle** (ppm) de chaque horloge.
5. **Drone muet** avec un **événement commun visible** (entrée, flash, lumières de piste), plus un cas où les vues
   sont très différentes (aérien/serré) pour mesurer le taux de propositions fausses.
6. **Formats variés** : 4K50 / 25 i/s, enregistreur 48 kHz et caméras 44,1/48 kHz, mono/stéréo.
7. **Vérité terrain** : pour chaque scène, le décalage vrai (même approximatif à l'image) pour calculer les erreurs.
