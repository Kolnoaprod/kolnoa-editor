# Mémoire de réalisation (« Director Memory ») — état des lieux, architecture, plan

Statut : **proposition à valider par ChatGPT et le réalisateur avant toute implémentation.** Cette PR contient ce document et un
**prototype jetable en lecture seule** (livrable 4 du cahier des charges) qui sert à valider le *format* du profil, pas à fonder le produit.
Rien n'est fusionné. Dépôt public : aucun nom de client ni de film ; on parle du « film de référence A ».

Principe directeur du cahier des charges, repris tel quel : **d'abord une mémoire explicite, mesurable et vérifiable**, pas un système d'apprentissage complexe. Un profil est un fichier de règles et de statistiques lisibles, avec leur provenance, que le réalisateur peut ouvrir, corriger, désactiver et supprimer. Aucun réentraînement de modèle.

Ce document prolonge `PROMPTS_ASSISTANT.md` (PR #25) : consignes ancrées au contenu, intentions typées, propositions, journal assistant. La mémoire de réalisation **réutilise** ces briques et n'en duplique aucune.

## 1. État des lieux : composants réutilisables

| Besoin du cahier des charges | Déjà dans le dépôt | Limite constatée |
| --- | --- | --- |
| Projet « natif » exploitable (plans, pistes, angles, durées) | `Timeline` (plans en images entières), `Session` (`base.json` + `history.jsonl`) | une seule timeline par projet ; pas de notion de projet « terminé » archivable |
| **Comparer la proposition avec ce que l'utilisateur a validé** (§4) | Le **journal de session** conserve chaque opération dans l'ordre, annulations comprises : c'est exactement la matière des corrections | rien ne distingue aujourd'hui une opération générée d'une opération manuelle ; il faut les étiqueter (propositions de la PR #25, PR E/G) |
| Rythme : durées de plans, fréquence des changements | `Clip.start/duration`, `Clip.group/angle` (un changement d'angle = deux plans contigus du même groupe, angles différents) | aucune statistique n'est calculée |
| Choix d'angles indépendants du numéro de caméra | `Angle`, `MulticamGroup`, statut/confiance de synchro, touches 1–9 | pas de **rôle d'angle** (large / moyen / serré / drone / réactions) : le numéro physique est la seule identité |
| Plans de coupe | pistes (`Track.role`), `Subclip` + `tags` du chutier, `place_subclip`, `search_bin` | **pas de « bibliothèque intelligente de rushes »** ni de plans extraits virtuellement des longues prises dans le dépôt ; le chutier n'a que des tags libres. Prérequis à clarifier avec le réalisateur |
| Structure en grandes séquences | `Marker` de nature `chapter` (déjà dans `MARKER_KINDS`) | aucun marqueur n'est posé ni exploité ; les repères ne suivent pas les opérations (voir PR #25, §3) ; pas de taxonomie des séquences |
| Transitions | fondus enchaînés **audio** (`Crossfade`) ; fondus de plan | **aucune transition vidéo** dans le moteur (prévue PR K de la PR #25) ; donc rien à apprendre côté image pour l'instant |
| Ralentis | — | **aucun champ de vitesse** dans `Clip` ; non mesurable aujourd'hui |
| Relation coupes ↔ musique | `Marker` `beat`/`phrase`, `Timeline.music` | aucune analyse musicale n'alimente les repères ; non mesurable aujourd'hui |
| Rapport en lecture seule sur de vrais médias | patron `sync-calibrate`, `audio-measure` (rapport, jamais d'écriture dans les rushes) | à reproduire pour les analyses visuelles (stabilité, valeur de plan) |
| Journal des échanges, ancrage au contenu, propositions typées | PR #25 (proposé, non codé) | dépendance directe pour §4 et §5 |
| Tâches en arrière-plan, local-first, Windows | `PeakService` (threads, cache disque atomique, purge), application d'essai Windows | rien de spécifique à l'analyse de projet |
| Films exportés sans projet natif | `from_decision_list` (une piste vidéo) | les liste de décisions donnent durées et ordre des plans, pas les angles, plans de coupe ni transitions |

**Ce que le dépôt permet déjà de mesurer honnêtement** sur un projet natif, sans aucune analyse visuelle : durées de plans par séquence, fréquence des changements de caméra, fréquence et durée des plans sur pistes secondaires, durée des fondus audio, durées par séquence (si les chapitres sont posés). **Ce qui ne l'est pas** et reste hors v1 : taille de plan (large/serré), raccords de regard/mouvement, émotion, ralentis, relation à la musique, valeur « émotionnelle » d'un plan.

## 2. Architecture proposée

Six modules du cahier des charges, tous locaux, tous sous `kolnoa_editor/memory/`, aucun ne touche aux rushes ni à la timeline sans passer par une proposition.

```
projet terminé ──▶ Reference Analyzer ──▶ ProfileStats (JSON, sans média) ─┐
(timeline native)                                                          ├─▶ Director Profile Manager ◀── observations validées
                                                                           │        (profils, versions, export)            ▲
nouveau tournage ─▶ analyse/synchro (existant) ─▶ Style-Aware Editing ◀────┘                                               │
                                                  Engine ─▶ Proposal (PR #25) ─▶ Validation & Explainability ─▶ montage ─▶ Correction Learning Engine
                                                                                  (aperçu, explication par règle)  (journal)   (compare proposé / validé)
Prompt Context Engine : résout « comme le film A » → profil + sections (via le contexte de la PR #25)
```

| Module | Rôle | Entrées → sorties |
| --- | --- | --- |
| **Reference Analyzer** | extrait des *statistiques et des exemples ancrés* d'un projet natif | `Timeline` (+ chapitres, + rôles d'angle) → `ProfileStats` |
| **Director Profile Manager** | profils : créer, enrichir, dupliquer, exporter, supprimer, combiner | `ProfileStats`, observations acceptées → `StyleProfile` versionné |
| **Correction Learning Engine** | transforme les écarts « proposé ↔ validé » en **observations** | journal de session + proposition → `Observation[]` (jamais d'écriture directe dans un profil) |
| **Style-Aware Editing Engine** | traduit un profil en paramètres/contraintes du générateur | `StyleProfile` + rushes analysés → `Intent[]` (PR #25) |
| **Prompt Context Engine** | comprend « mêmes structure et rythme que le film A, danses du film B » | texte + profils → sélection de `Rule` par section |
| **Validation & Explainability** | aperçu, tableau des décisions, justification de chaque décision par la règle qui l'a produite | proposition → rapport + pas d'annulation unique |

### 2.1 Modèles de données

Tout est du JSON versionné (`schema`), lisible, sans média ni empreinte vidéo : **le profil fonctionne quand les rushes du film de référence ne sont plus accessibles** (§8). Les exemples sont des *références* (projet, section, plage en images, plan), jamais des images.

```
ProfileStats                       # résultat brut d'une analyse, immuable, daté
  schema, analyzer_version, source{ref_id, fps, n_clips, content_fingerprint}   # empreinte du contenu analysé, pas des médias
  sections[]: SectionStat
  global: aggregates (nombre de plans, durée totale)
  warnings[], confidence (par statistique : n d'échantillons, couverture)

SectionStat
  kind          # taxonomie ouverte : preparatifs, mairie, ceremonie, cocktail, reception, danses, … (texte libre accepté)
  start, end    # images de timeline ; duration_s
  shots         # n, quantiles de durée (min, p10, p25, médiane, p75, p90, max), moyenne
  cuts_per_min
  angle_switches_per_min        # changements d'angle à l'intérieur d'un groupe multicam
  angle_roles{role: part du temps}   # si rôles renseignés ; sinon absent (jamais deviné)
  cutaways{n, per_min, duration quantiles, tracks}   # plans sur pistes secondaires (v1 : critère structurel documenté)
  crossfades{n, duration quantiles}                    # audio seulement en v1
  hold_ratio    # part du temps passée sur des plans plus longs que le seuil « respire » (seuil explicite, réglable)
  confidence    # f(n plans)

StyleProfile
  id, name, version, parent_version, created, updated
  sources[]     # ProfileStats utilisés (ref_id, date, empreinte)
  structure     # séquences types : ordre observé, durées relatives, optionnelles/obligatoires — jamais imposées
  rules[]       # Rule : règle explicite
  stats{}       # ProfileStats agrégés par kind de section, avec n et confiance
  examples[]    # références ancrées
  history[]     # Observation acceptées / rejetées / modifiées (traçabilité complète)

Rule
  id, text (français lisible), scope (global | section_kind:X | projet:P), params (ex. {"shot_p50_s": [2.1, 3.4]}),
  kind (contrainte dure | préférence), confidence, provenance[] (ref_id, plages), status (active | désactivée),
  introduced_in_version

Observation                          # issue de l'apprentissage des corrections
  id, scope (ponctuelle | section_kind | générale), type (allonger_plan, remplacer_angle, alléger_transition, …),
  evidence[] (occurrences ancrées : proposé → validé), count, confidence, technical_cause (ex. « angle instable », « média défectueux »),
  status (en attente | acceptée | rejetée | modifiée), proposed_rule, decided_by, decided_at
```

**Garde-fous de modèle** : `Observation.status = en attente` par défaut ; **seule une action explicite** du réalisateur la transforme en `Rule` (`« Aucune correction isolée ne doit modifier le profil global »`). Chaque `Rule` pointe vers sa provenance et chaque version de profil est conservée (retour arrière = ré-activer la version précédente, jamais une réécriture).

### 2.2 Rôle d'angle plutôt que numéro de caméra

Pour apprendre « une logique de sélection indépendante du numéro de caméra », le réalisateur affecte **une fois par tournage** un rôle à chaque angle (ex. `large`, `moyen`, `serré`, `drone`, `réactions`, `plan fixe`). Le profil ne parle que de rôles. Un rôle non renseigné reste « inconnu » : il n'est jamais deviné. L'inférence automatique de la taille de plan est **expérimentale** et ne sera proposée qu'après un rapport sur vrais rushes.

### 2.3 Séquences : posées par l'humain d'abord

La détection automatique de grandes séquences est un problème ouvert et non vérifié. v1 : les sections sont des **repères `chapter`** posés par le réalisateur (ou déduits des plages d'un fichier de sections). La détection automatique viendra en proposition à valider, étiquetée avec sa confiance.

### 2.4 Apprendre des corrections (§4)

Source : le **journal de session** de la proposition générée. Les opérations issues de la proposition sont étiquetées (PR #25 E/G) ; toutes les opérations manuelles qui suivent sont comparées à l'état proposé et classées en types d'écart (plan prolongé, plan de coupe raccourci, angle remplacé, transition supprimée, plan réintégré…). Règles de portée, **explicites et réglables** :
1. une occurrence : `ponctuelle` (valable pour ce projet seulement) ;
2. répétée dans ≥ N occurrences d'un même type de section d'un projet : candidate `préférence de section` ;
3. répétée dans ≥ M projets : candidate `préférence générale` ;
4. si la cause est technique (angle marqué instable ou non synchronisé, média manquant, confiance faible), l'observation est étiquetée `contrainte technique`, **jamais** une préférence artistique.
Aucune promotion n'est automatique : le panneau « Ce que Kolnoa Editor a appris » liste les candidates avec leurs preuves ; accepter / modifier / rejeter est le seul chemin.

### 2.5 Génération et explicabilité

Le moteur applique le profil en **contraintes et préférences** sur le générateur de premier montage (préparation multicam, plans de coupe, transitions), jamais en copiant des images. Chaque décision de la proposition porte la règle qui l'a produite (`R012`) ; le rapport avant application affiche : structure et durée par partie, décisions multicam principales, catégories de plans de coupe, transitions, éléments du film de référence réellement reproduits (règles utilisées), incertitudes et séquences absentes. Actions : prévisualiser, appliquer (**une** transaction réversible), modifier les consignes, annuler. Les séquences absentes du nouveau tournage ne sont **pas** inventées : elles sont listées comme « non trouvées ».

## 3. Protocole de comparaison avec / sans profil (livrable 5)

**Objectif** : mesurer, sur le même jeu de rushes, ce que le profil change, avec des critères fixés *avant* de regarder les résultats.

*Conditions comparées* (mêmes rushes, mêmes synchros validées, même graine) :
- **B0** — premier montage sans profil (heuristique de base du générateur) ;
- **B1** — avec le profil du film de référence A ;
- **H** — montage final réalisé par le réalisateur (vérité terrain, quand elle existe).

*Deux montages de test* : (1) **validation croisée** : le profil est appris sur d'autres projets que le film à reproduire, puis comparé à son montage réel (évite d'apprendre sur le test) ; (2) **nouveau tournage** : B0 et B1 présentés en **aveugle** au réalisateur (ordre aléatoire, aucun libellé), qui note et corrige.

*Métriques* (calculées par le même code que l'analyseur, donc vérifiables) :

| Axe | Métrique |
| --- | --- |
| Structure | distance d'édition entre l'ordre des sections ; écart de durée par section (%) |
| Rythme | distance de Kolmogorov–Smirnov entre distributions de durée de plans, par type de section ; écart de coupes/min |
| Angles | accord par rôle d'angle sur l'échantillonnage régulier du temps (% d'images sur le même rôle que H) ; nombre de changements d'angle « inutiles » (aller-retour sous seuil) |
| Plans de coupe | précision/rappel des insertions à ±N images de H ; écart de durée |
| Transitions | accord de type et de durée (audio en v1) |
| Stabilité | part du temps sur des angles non marqués instables *(consultatif tant que non vérifié)* |
| **Effort de correction** | nombre d'opérations manuelles et d'images modifiées par le réalisateur pour passer de la proposition au montage final (métrique principale en usage réel) |
| Validité | 0 refus du moteur à l'application ; rushes inchangés (empreintes avant/après) ; déterminisme (même entrées ⇒ même proposition) |

*Honnêteté statistique* : avec un seul film de référence, les écarts ne sont **pas** généralisables. Le rapport donne chaque métrique avec un intervalle (rééchantillonnage des plans, *bootstrap*) et son nombre d'échantillons, et ne conclut qu'à partir de seuils décidés à l'avance ; sous le seuil de couverture il écrit « non concluant ».

## 4. Découpage en PR

Chaque PR : indépendante, en brouillon, tests automatisés, documentation, critères de validation. Aucune fusion sans accord.

| PR | Contenu | Tests | Critères de validation | Dépend de |
| --- | --- | --- | --- | --- |
| **M0 (cette PR)** | Plan + prototype jetable `memory/analyzer.py` (lecture seule) | statistiques sur timelines synthétiques à valeurs connues | le format de `ProfileStats` est jugé suffisant par le réalisateur ; aucune écriture dans la timeline | — |
| **M1 — Analyseur de référence** | version durcie : chapitres, rôles d'angle, filtrage des plans sans intérêt, seuils réglables, rapport lisible, CLI `profile-analyze`, rapport sur vrais projets (lecture seule, comme `sync-calibrate`) | propriétés (invariance au déplacement global, additivité) ; projets incomplets, médias manquants, sections absentes | résultats exacts sur cas construits à la main ; rapport sur un vrai projet relu par le réalisateur | M0 |
| **M2 — Gestionnaire de profils** | `StyleProfile`, règles, versions, dupliquer / exporter / importer / supprimer, combinaison par section (« structure de A, rythme des danses de B ») | aller-retour JSON ; combinaison déterministe ; retour à une version | aucune perte à l'export/import ; suppression propre | M1 |
| **M3 — Rôles d'angle** | affectation par tournage (jamais devinée), persistée, utilisée par l'analyseur | rôle absent ⇒ « inconnu » ; migration | jamais d'inférence silencieuse | M1 |
| **M4 — Observations et panneau « appris »** | modèle `Observation`, états, provenance, retour arrière, panneau (logique sans écran + UI) | une correction isolée ne change jamais le profil ; accepter/rejeter/modifier ; versionnage | aucune règle ne naît sans décision explicite | M2 |
| **M5 — Apprentissage des corrections** | comparateur proposé ↔ validé sur le journal, classification des écarts, portée ponctuelle / section / générale, étiquette de cause technique | écarts construits à la main ; causes techniques non promues en préférences | scénarios d'acceptation 5 et 6 | M4, PR #25 (E, G) |
| **M6 — Générateur sensible au style** | profil → contraintes/préférences du premier montage : structure, multicam (plans stables, rôles), plans de coupe par catégorie, transitions | validité (0 refus), déterminisme, respect des règles dures, séquences absentes listées | scénarios 2, 3, 4 ; comparaison B0/B1 produite | M2, M3, PR #25 E |
| **M7 — Contexte de prompt** | références à d'autres projets dans les consignes (« comme le film A, danses du film B ») | résolution de références ambiguës ⇒ question | scénario 7 | M2, PR #25 F |
| **M8 — Validation et explicabilité** | rapport avant application, explication par règle, Appliquer/Annuler en une transaction | un seul pas d'undo ; annuler = état antérieur identique | scénario 8 | M6, PR #25 G |
| **M9 — Banc de comparaison** | harnais du §3 : B0/B1/H, métriques, rapport avec intervalles, présentation en aveugle | métriques sur cas connus ; bootstrap reproductible | protocole exécutable de bout en bout sur données synthétiques | M6 |
| **M10 — Robustesse** | médias manquants, projets incomplets, profil sans accès au film de référence, rushes intacts | scénarios 9 et 10 | empreintes inchangées | M6 |

Dépendances externes à trancher : la **bibliothèque intelligente de plans de coupe** (absente du dépôt), les transitions vidéo (PR K de #25), la détection de séquences, l'analyse de stabilité (PR I de #25).

## 5. Risques et décisions demandées

- **Peu de données** : un film = un échantillon ; les profils doivent afficher `n` et leur confiance, et rester des *guides*, pas des lois.
- **Préférence ou contrainte ?** Un plan évité parce que le rush est défectueux n'est pas un goût ; la cause technique est enregistrée avec l'observation.
- **Films exportés seuls** : n'apportent que la structure grossière et les durées ; pas d'angles ni de plans de coupe.
- **Respect de la vie privée** : le profil ne contient ni média, ni nom de fichier source, ni nom de client ; il est exportable.

Décisions : (1) sections posées par l'humain en v1, détection automatique plus tard ; (2) rôles d'angle affectés par le réalisateur ; (3) seuils de promotion N et M réglables (valeurs par défaut à fixer avec toi) ; (4) bibliothèque de plans de coupe : à concevoir dans une PR dédiée ou existe-t-elle ailleurs ? ; (5) le prototype M0 : conserver, durcir (M1) ou jeter.
