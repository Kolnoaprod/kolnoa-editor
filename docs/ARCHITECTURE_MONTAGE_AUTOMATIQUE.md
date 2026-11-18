# Architecture « montage automatique + prompts + édition manuelle » — réévaluation (2026-11-18)

Remplace les interprétations précédentes (la proposition `PROMPTS_ASSISTANT.md` traitait le prompt comme une couche posée sur un montage fait à la main ; le plan `BETA_AVANCEMENT.md` mettait le rendu et l'application avant tout). **Document de conception : aucun code produit écrit, rien à fusionner.** Budget supplémentaire : 0 €.

## 1. Définition retenue
Kolnoa Editor = **montage principalement automatique** + **timeline visuelle toujours éditable**, par prompts **ou** à la main, sur **le même projet**. « Monte-moi la Houppa » produit un montage complet, visible sur la timeline **pendant** sa construction ; ensuite le réalisateur retouche une zone (entrée/sortie + consigne) ou directement les plans.

## 2. Principe d'architecture : un seul moteur d'édition, trois « auteurs »
```
 Rushes ─▶ Registre (A1-1) ─▶ Analyse locale ─▶ ┐
                                                 │            ┌─ Monteur automatique ─┐
 Brief / consigne ─▶ Interpréteur local ────────▶│  Intentions │  (génère)             │
 Sélection (point, entrée/sortie, plans) ───────▶│  typées     ├─ Directives ciblées ──┤─▶ Proposition ─▶ garde de portée ─▶ Transaction ─▶ Session
 Souris / clavier ──────────────────────────────▶┘             └─ Édition manuelle ────┘     (aperçu, diff)   + provenance       (journal, annuler)
```
- **Une seule source de vérité** : `Session` (timeline + journal). Les trois auteurs produisent des **`Transaction`** appliquées par `session.do(...)` : même validation, même annuler/rétablir, même persistance. Aucun état parallèle « côté IA ».
- **Primitives partagées** : l'édition manuelle, le monteur et les directives appellent les **mêmes** constructeurs (`razor`, `move_clips`, `trim_edge`, `ripple_delete`, `delete_range`, `place_clip`, `replace_clip`, `cut_to_angle`, `switch_angle`, `place_subclip`). Ce qu'on peut faire à la souris, le moteur peut le faire, et inversement ; rien de nouveau à inventer côté timeline.
- **Origine par transaction** (`manuel` | `auto` | `directive`, + numéro de consigne) : champ **additif** dans l'entrée du journal (pas de changement du schéma de timeline ni des opérations).

## 3. Les briques et leur raccord
| Brique | Rôle | Base existante |
|---|---|---|
| **Analyse locale** (sans API) | par rush : activité de parole/silence, qualité audio, stabilité/mouvement, changements de plan, netteté/exposition ; stockée en cache, relançable, jamais d'écriture sur le rush | `scanner`, `proxies`, `audio_quality`, `sync` (stabilité déjà prévue), tâches de fond #12 |
| **Synchro multicam** | axe de groupe commun | PR #9, `Angle.to_group` |
| **Monteur automatique** | `plan(analyse, brief, style) → Proposition` : structure (chapitres issus des dossiers/marqueurs/plan du réalisateur), choix des plans, **choix des angles** (`record_angle_switches`, qualité audio), rythme (durées de tenue par type de séquence), plans de coupe | `multicam_live`, chutier, mémoire de réalisation (#26) comme *paramètres de style* |
| **Sélection** (`Selection`) | point, entrée/sortie, ensemble de plans, pistes concernées ; **un seul modèle partagé** par la souris et les prompts | à créer (UI) |
| **Interpréteur de consignes** | consigne + sélection → **intention typée** (`raccourcir(durée|%)`, `remplacer_par_plan_de_coupe`, `accélérer_rythme`, `supprimer`, `changer_angle`, …) ; **règles locales** + clarification ; repli : fichier d'opérations rédigé dans ChatGPT/Claude et importé (`timeline-apply`) | `Directive` (#25), aucun appel payant |
| **Garde de portée** | vérifie que la proposition ne modifie que la zone ciblée (+ conséquences de ripple **déclarées**) et **aucun plan protégé** | à créer ; s'appuie sur `Clip.locked` / `Track.locked` existants |
| **Aperçu / diff** | avant–après sur la timeline, accepter/refuser, annulable d'un geste | UI |
| **Ancres A1** | uniquement pour les consignes **persistantes** (qui doivent survivre à des modifications ultérieures) ; `ConfirmedTarget` si une cible est ambiguë | #27–#29 |

**Précision sur les ancres** : une commande **interactive** (« raccourcis cette zone ») s'exécute **tout de suite** sur les positions actuelles de la sélection, avec un contrôle de fraîcheur (jeton de timeline) — pas besoin d'ancres. Les ancres servent aux consignes **mémorisées** et aux propositions asynchrones. Cela simplifie la 1re version.

## 4. Protéger le travail manuel (garantie centrale)
Trois états par plan, tenus dans un **fichier annexe de la session** (`provenance.json`, reconstructible depuis le journal), indexés par id et filiation (`parent`), donc stables aux coupes :
- `auto` : produit par le monteur ou une directive, jamais touché à la main ;
- `manuel` : modifié par le réalisateur (toute transaction `manuel` marque les plans touchés) ;
- `validé` : accepté explicitement (verrou doux).
**Règles** : une transaction `auto`/`directive` **ne peut pas** modifier un plan `manuel` ou `validé` hors de sa portée — et **dans** sa portée, elle doit le **signaler et demander confirmation** (« 3 plans retouchés par vous seront remplacés »). Une relance globale (« remonte tout ») ne réécrit que les plans `auto`. Tout reste annulable : une génération complète = **un groupe d'annulation** (un Ctrl+Z la retire) tout en gardant un pas par section.

## 5. Génération pendant que la timeline reste utilisable
Le monteur travaille **en tâche de fond** (#12), **section par section** : chaque section terminée est appliquée comme une transaction `auto` → elle **apparaît** sur la timeline. La section en cours est **verrouillée en lecture seule** ; le reste est libre d'édition. Si une modification manuelle invalide une section en cours (jeton périmé), la section est **recalculée ou abandonnée avec un message**, jamais appliquée de travers. Annulation de la génération à tout moment.

## 6. « Monte-moi la Houppa » : de la phrase au périmètre
La consigne est résolue en **sous-ensemble de rushes + structure** à l'aide des métadonnées déjà inventoriées (dossier de premier niveau — ex. `PREPA HOUPPA RECEPTION` —, appareil, dates), des marqueurs et du brief ; correspondance approchée **avec confirmation** (« je comprends : dossiers X, Y — correct ? »). Rien de spécifique au mariage dans le moteur : les **chapitres, durées de tenue et préférences** sont des paramètres (gabarit/profil de style) → applicable à d'autres productions ; seul le mariage est validé par des essais.

## 7. Limites assumées (sans API payante)
- L'analyse locale mesure (parole, stabilité, qualité, rythme) mais **ne « comprend » pas** une scène (visages, émotions, rituels). Le gros du travail automatisable = structure, synchro, angles, plans propres, cadence ; la sémantique vient du **brief, des dossiers, des marques et des corrections** (mémoire de réalisation, #26).
- La qualité du premier montage est **inconnue tant qu'on ne l'a pas comparée à un montage du réalisateur sur de vrais rushes** : protocole de comparaison déjà écrit (#26). Promettre « le gros du travail » avant cette mesure serait prématuré.
- Modèles locaux gratuits (reconnaissance de plans, parole) : possibles plus tard, mais lourds à installer et sans garantie sur la machine → **optionnels**, hors première bêta.

## 8. Réévaluation de l'existant
| Existant | Rôle dans la nouvelle architecture | Action |
|---|---|---|
| `Session`/`Op`/`Transaction`, journal, annuler | **cœur commun** | ajouter `origine` au journal (additif) + groupes d'annulation |
| Builders d'édition, multicam live, chutier | **primitives partagées** monteur/directives/manuel | réutiliser tels quels |
| Synchro multicam (#9) | prérequis du monteur | calibrer sur vrais rushes |
| Audio : source maître, mixage (#16) | choix d'audio du monteur, rendu | réutiliser |
| Ancres A1 (#27–#29) | consignes persistantes, cibles ambiguës | conservées ; **non bloquantes** pour la 1re version interactive |
| Interface Qt (timeline, lecteur) | base de la vraie application | à **étendre** : sélection entrée/sortie, panneau de consignes, aperçu/diff, génération visible |
| Tâches de fond (#12) | génération progressive | à fusionner (sans conflit) |
| Rendu | export | **J1** reste nécessaire (le résultat doit sortir en MP4) |
| Couleur (#11) / transitions (#6/#7) | étapes de montage secondaires | **après** le cœur auto/édition |
| Mémoire de réalisation (#26) | paramètres de style du monteur | exploitée après essais réels |

## 9. Plan révisé (chaque ligne = PR brouillon à valider avant démarrage)
| Étape | Contenu | Pourquoi dans cet ordre |
|---|---|---|
| 0 | Consolidation : fusions dans l'ordre déjà proposé, #12 incluse | socle unique |
| 1 | **Application + modèle de sélection partagé + origine/provenance** (édition manuelle utilisable, enregistrer/rouvrir, entrée/sortie) | sans elle ni prompt ni monteur n'ont de support visible |
| 2 | **Analyse locale** (cache, tâche de fond, sur proxys) | donnée d'entrée du monteur ; mesurable seule |
| 3 | **Monteur automatique v1** : structure + synchro + angles + rythme → transactions `auto` par section, visibles pendant la génération | **fonction centrale** ; évaluée sur vrais rushes |
| 4 | **Directives ciblées** : entrée/sortie + consigne → raccourcir, remplacer par plan de coupe, accélérer, supprimer ; garde de portée, aperçu, confirmation | réutilise primitives et sélection |
| 5 | **Rendu complet** (J1-a→d) | peut démarrer **dès l'étape 0** (J1-a est pur) ; indispensable à l'export |
| 6 | Couleur/LUT, transitions ; plans de coupe en bibliothèque | enrichissements |
| 7 | Essais réels, bêta Windows | mesures 4K50/GPU, comparaison au montage du réalisateur |

## 10. Décisions demandées
1. Valider l'architecture (§2–§5) et le **plan révisé** (§9), en particulier : monteur automatique **avant** couleur/transitions, application **avant** monteur.
2. Provenance dans un **fichier annexe + champ d'origine du journal** (recommandé) plutôt que dans `Clip.extra`.
3. Première version de l'assistant **sans compréhension sémantique** (§7), avec évaluation sur vrais rushes avant toute promesse.
4. Autorisation de démarrer l'étape 0, puis les étapes 1 et 5-a en parallèle.
