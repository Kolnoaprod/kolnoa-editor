# Ancres de contenu (A1) — référence

Implémente `docs/A1_ANCRES_PROPOSITION.md`. Aucune modification du schéma de la timeline ni des opérations du moteur.

## Modules (`src/kolnoa_editor/anchors/`)
| Module | Rôle |
|---|---|
| `registry.py` (A1-1) | `media_id` UUID opaque/immuable ; déplacement et réimport par proposition + confirmation humaine. |
| `timebase.py` | Entiers uniquement (µs). `rdiv` = arrondi au plus proche, demis vers le haut. Aller-retour image ⇄ µs exact. |
| `anchor.py` | `ContentAnchor` (point), `RangeAnchor` (plage d'UNE source), `anchor_at`, `range_between`, refus (`AnchorRefused`). |
| `resolver.py` | `Resolver(tl, registry).resolve(ancre)` → `Resolution` (état typé, options = collections de segments). |
| `confirm.py` | `ConfirmedTarget` : seule cible applicable ; `Resolution.confirm(choix)`. |

## États
| État | Sens | Confirmation requise |
|---|---|---|
| `exacte` | un emplacement, couverture complète, contexte identique | aucune (`confirm()`) |
| `contexte_modifié` | trouvée, voisinage changé | `Acknowledge()` |
| `multiple` | même contenu utilisé plusieurs fois | `ChooseOccurrence(i)` |
| `fragmentée` | plage partielle, trouée ou répartie | `AcceptFragments()` |
| `hors_plan` | média utilisable, instant non utilisé dans le montage | `AcceptNearestEdge()` (bord de plan le plus proche) |
| `perdue` | média manquant/remplacé/inconnu, groupe supprimé | **jamais** |
| `non_supportée` | VFR, cadence non entière, vitesse ≠ 1 sur AU MOINS un plan de cette source (on ne choisit pas « les autres ») | **jamais** |

Précédence quand plusieurs s'appliquent : non_supportée > perdue > fragmentée > multiple > hors_plan > contexte_modifié > exacte ; les autres restent dans `flags` (`doublon`, `dispersée`).

## Garanties
- Une cible n'existe que via `Resolution.confirm` ; `ConfirmedTarget(...)` direct lève `TypeError`. Un choix superflu ou inadapté est refusé.
- `check_fresh(cible, resolver)` refuse une cible calculée sur une timeline modifiée depuis (`StaleTarget`).
- Une coupe au rasoir (±1 µs d'arrondi) ne crée ni faux « multiple » ni « contexte modifié » : tolérance de jonction `JOIN_TOL_US = 2`, l'instant de la coupe appartient au plan de droite.
- Axe `group` : les instants sont en temps du groupe (offset + dérive des angles) ; un changement d'angle ne déplace pas l'ancre.
- Aucun média n'est lu ni écrit.

## Limites connues (volontaires)
- Cadences médias non entières (23,976 / 29,97), VFR et changements de vitesse : refus explicite, pas d'approximation.
- Une plage ne couvre qu'une source continue ; plusieurs plans = plusieurs ancres.
- Marqueurs : inchangés (images fixes) — traité par la PR R, hors A1.
- Performances : mesures en A1-3.
