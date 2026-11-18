# Couleur non destructive (PR C0)

Un réglage de couleur est une donnée du projet : les rushes ne sont jamais modifiés, le rendu applique les filtres.

## Portées et empilement
| Portée | Exemple | Effet |
| --- | --- | --- |
| `global` | `global` | tout le film |
| angle | `angle:g001/CAM2` | un angle d'un groupe multicam, partout où il apparaît |
| plan | `clip:s012` | un seul plan |

Pour un plan, les réglages **s'empilent** dans l'ordre global, angle, plan. Un plan issu d'une scission hérite du réglage de son plan d'origine tant qu'il n'a pas le sien. Le réglage d'un plan supprimé reste dormant (l'annulation le retrouve) et est signalé comme orphelin.

## Un réglage
`input_profile` (auto, rec709, s-cinetone, s-log3, hlg, autre), `lut` (chemin `.cube`, espaces d'entrée/sortie déclarés, `rights`), `lut_strength` (0 à 1), `exposure_ev` (−4 à 4), `temperature_k` (> 0 plus chaud), `tint` (> 0 vers le magenta), `contrast`, `saturation` (−100 à 100), `enabled`.

Ordre fixe : LUT (mélange selon l'intensité), exposition, température et teinte, contraste et saturation. Le filtre est placé avant le `pad` : les bandes noires ne sont pas étalonnées. L'aperçu et le rendu utilisent la même chaîne (`grade.py`).

## Prudence
- `auto` veut dire **non confirmé** : aucun profil LOG n'est supposé, aucune conversion n'est inventée. Un profil `s-log3` sans LUT de conversion est signalé (« l'image restera en LOG »).
- Cette PR ne contient pas de conversion d'espace colorimétrique : elle passe par une LUT technique dont les espaces sont déclarés. Une LUT dont les espaces ne correspondent pas au profil déclaré est signalée.
- Une LUT n'est jamais copiée dans le projet (seul son chemin est gardé) ; ses droits d'utilisation sont `unknown` tant que `--lut-rights-confirmed` n'est pas donné, et le signalement reste affiché.
- Aucune harmonisation automatique ici (`color-match` : PR C4, avec mesures sur des passages simultanés).

## Commandes
```
kolnoa color-set <mariage> clip:s012 --exposure 0.5 --temperature 300 --saturation -5
kolnoa color-set <mariage> angle:g001/CAM2 --lut LUTS/doux.cube --lut-input rec709 --lut-output rec709 --lut-strength 0.8
kolnoa color-show <mariage>      # réglages + avertissements
kolnoa color-clear <mariage> clip:s012
kolnoa timeline-undo <mariage>   # annule aussi la couleur
```
Le rendu multicam (`timeline-render` d'une timeline multicam) reste refusé : PR D. Les réglages d'angle sont déjà enregistrés et empilés pour ce moment.

## Ce qui n'est pas mesuré
Les effets sont vérifiés sur des images synthétiques (pixels mesurés). Aucune validation sur de vrais rushes Sony ou drone : à faire avec les métadonnées réelles avant de proposer un profil automatique.
