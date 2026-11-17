# Mémoire de réalisation (« Director Memory ») — état des lieux, architecture, plan

Statut : **plan révisé après la revue de ChatGPT et les arbitrages du réalisateur ; aucune implémentation du produit avant son accord explicite.**
Cette PR contient ce document et un **prototype expérimental isolé en lecture seule** (`kolnoa_editor/experimental/memory_proto/`) :
oracle de tests et banc de comparaison, **pas** le moteur de production (voir §1 bis). Rien n'est fusionné. Dépôt public : aucun nom de client ni de film ; on parle du « film de référence A ».

Principe directeur du cahier des charges, repris tel quel : **d'abord une mémoire explicite, mesurable et vérifiable**, pas un système d'apprentissage complexe. Un profil est un fichier de règles et de statistiques lisibles, avec leur provenance, que le réalisateur peut ouvrir, corriger, désactiver et supprimer. Aucun réentraînement de modèle.

Ce document prolonge `PROMPTS_ASSISTANT.md` (PR #25) : consignes ancrées au contenu, intentions typées, propositions, journal assistant. La mémoire de réalisation **réutilise** ces briques et n'en duplique aucune.

## 1. État des lieux : composants réutilisables

| Besoin du cahier des charges | Déjà dans le dépôt | Limite constatée |
| --- | --- | --- |
| Projet « natif » exploitable (plans, pistes, angles, durées) | `Timeline` (plans en images entières), `Session` (`base.json` + `history.jsonl`) | une seule timeline par projet ; pas de notion de projet « terminé » archivable |
| **Comparer la proposition avec ce que l'utilisateur a validé** (§4) | Le **journal de session** conserve chaque opération dans l'ordre, annulations comprises : c'est exactement la matière des corrections | rien ne distingue aujourd'hui une opération générée d'une opération manuelle ; il faut les étiqueter (propositions de la PR #25, PR E/G) |
| Rythme : durées de plans, fréquence des changements | `Clip.start/duration`, `Clip.group/angle` (un changement d'angle = deux plans contigus du même groupe, angles différents) | aucune statistique n'est calculée |
| Choix d'angles indépendants du numéro de caméra | `Angle`, `MulticamGroup`, statut/confiance de synchro, touches 1–9 | pas de **rôle d'angle** (large / moyen / serré / drone / réactions) : le numéro physique est la seule identité |
| Plans de coupe | pistes (`Track.role`), `Subclip` + `tags` du chutier, `place_subclip`, `search_bin` | **pas de bibliothèque intelligente** : le chutier n'a que des tags libres, sans qualité, sans rôle narratif, sans provenance. **Décision du réalisateur : PR dédiée « L » avant M6** (§2.6) |
| Structure en grandes séquences | `Marker` de nature `chapter` (déjà dans `MARKER_KINDS`) | aucun marqueur n'est posé ni exploité ; les repères ne suivent pas les opérations (voir PR #25, §3) ; pas de taxonomie des séquences |
| Transitions | fondus enchaînés **audio** (`Crossfade`) ; fondus de plan | **aucune transition vidéo** dans le moteur (prévue PR K de la PR #25) ; donc rien à apprendre côté image pour l'instant |
| Ralentis | — | **aucun champ de vitesse** dans `Clip` ; non mesurable aujourd'hui |
| Relation coupes ↔ musique | `Marker` `beat`/`phrase`, `Timeline.music` | aucune analyse musicale n'alimente les repères ; non mesurable aujourd'hui |
| Rapport en lecture seule sur de vrais médias | patron `sync-calibrate`, `audio-measure` (rapport, jamais d'écriture dans les rushes) | à reproduire pour les analyses visuelles (stabilité, valeur de plan) |
| Journal des échanges, ancrage au contenu, propositions typées | PR #25 (proposé, non codé) | dépendance directe pour §4 et §5 |
| Tâches en arrière-plan, local-first, Windows | `PeakService` (threads, cache disque atomique, purge), application d'essai Windows | rien de spécifique à l'analyse de projet |
| Films exportés sans projet natif | `from_decision_list` (une piste vidéo) | les liste de décisions donnent durées et ordre des plans, pas les angles, plans de coupe ni transitions |

**Ce que le dépôt permet déjà de mesurer honnêtement** sur un projet natif, sans aucune analyse visuelle : durées de plans par séquence, fréquence des changements de caméra, fréquence et durée des plans sur pistes secondaires, durée des fondus audio, durées par séquence (si les chapitres sont posés). **Ce qui ne l'est pas** et reste hors v1 : taille de plan (large/serré), raccords de regard/mouvement, émotion, ralentis, relation à la musique, valeur « émotionnelle » d'un plan.

## 1 bis. Le prototype M0 : ce qu'il est, ce qu'il mesure

**Statut décidé** : outil expérimental **isolé** (`kolnoa_editor/experimental/`), utile pour valider les calculs, fabriquer des jeux de test et comparer avant/après. Un test vérifie qu'**aucun module de production ne l'importe**. Il ne deviendra pas le moteur par accumulation de fonctions : après validation sur des cas réels, on décide en M1 quelles parties sont **réimplémentées proprement**.

Métriques réellement mesurées (définitions structurelles, pas sémantiques) :

| Métrique | Définition exacte | Ce que ce n'est PAS |
| --- | --- | --- |
| Section | repère `chapter` jusqu'au repère suivant ; texte = type de séquence | une détection automatique de séquences |
| Plan | clip d'une piste vidéo de rôle `main`, rattaché à la section où il **commence** (un plan qui déborde reste dans sa section) | un plan « cinématographique » (deux pistes `main` superposées comptent deux fois : limite connue, testée) |
| Durée, quantiles | `duration / fps`, arrondie à 3 décimales (1/30 s ⇒ 0,033) | une durée perçue |
| Coupes/min | nombre de plans de la section ÷ minutes de la section | le rythme ressenti |
| Changement d'angle | deux plans **contigus** du même groupe, angles différents | un choix artistique justifié |
| « Respire » | part du temps dans des plans ≥ seuil explicite (6 s par défaut, réglable) | un jugement d'émotion |
| Plan de coupe | clip d'une piste vidéo dont le rôle n'est pas `main` | **une vérité sémantique** : un plan posé sur la piste principale n'est pas vu comme plan de coupe, et réciproquement (testé) |
| Fondus | fondus enchaînés **audio** seulement (aucune transition vidéo dans le moteur) | les transitions d'image |

Non mesuré, jamais deviné : taille de plan, raccords de regard/mouvement, ralentis, rapport à la musique, émotion.
Cadences testées : 24, 25, 30 et 50 i/s (entiers : le 23,976 / 29,97 n'est pas supporté par le modèle, à refuser explicitement plutôt qu'à approcher). Tests ajoutés : trous, chevauchements, transitions, annulation/rétablissement via une vraie `Session`, pistes secondaires, scission au rasoir.

**Anonymisation : liste blanche, pas « copier puis effacer »** (revue ChatGPT). L'export est **construit champ par champ** à partir d'une table de décisions (`FIELD_POLICY` : conservé / remplacé par un identifiant généré / non exporté). Un test échoue si un champ est ajouté au modèle sans décision : la confidentialité n'est jamais supposée par défaut. Sont couverts, en plus des noms de médias : noms des bandes audio, identifiants et rôles de pistes (rôle inconnu ⇒ `autre`), clés arbitraires de `music` (seules `path`→`musique.wav`, `start`, `gain_db`, `fade_out` survivent), identifiants de plans/liens/groupes/angles/fondus, étiquettes, notes, `extra`, chutier, textes de repères. **Textes de chapitres : assainis** (seuls des types de séquence génériques passent ; le reste devient `section-N`, N = ordre de première apparition ; ou libellé choisi par le réalisateur via `--chapters-map`). Un **lint récursif** du document FINAL refuse toute clé hors liste blanche, toute chaîne libre et tout terme de la liste `--deny` (noms de clients, lieux : insensible à la casse et aux accents), une première fois sur le dictionnaire, une seconde sur le texte réellement sérialisé ; en cas de refus **rien n'est écrit**. Le rapport imprimé ne contient aucun nom d'origine. Limite assumée : la liste blanche garantit la structure, pas l'absence d'indices indirects (une durée inhabituelle peut rappeler un film) ; **relecture par le réalisateur obligatoire avant tout partage**.

**Protection des fichiers de session.** La commande n'ouvre jamais une `Session` (qui réécrit `current.json`) : elle lit `current.json` ou un fichier JSON, en lecture seule. Le chemin de sortie est refusé s'il est : dans le dossier de session source, dans n'importe quel dossier de session (présence de `base.json`/`history.jsonl`/`current.json`), le fichier source lui-même, un nom de fichier de session, un lien symbolique, ou un fichier existant sans `--force` explicite ; l'écriture est atomique (fichier temporaire voisin puis remplacement). Test : octets **et** dates de modification de la session sont identiques avant/après toutes les commandes. Garde-fous du dépôt public : `.gitignore` exclut les fichiers de session et `anonymized_*.json` ; un test échoue si un fichier de session est suivi par git, ou si une fixture réelle est ajoutée dans `tests/data/anonymized/` sans `AUDIT.md` écrit et sans lint propre. **Aucun vrai fichier de tournage ne doit être publié sans audit.**

**Seuil « respire »** : `hold_s` reste un paramètre (6,0 s par défaut), configurable **par type de section** (`--hold-by-kind fichier.json`, clé `default`) ; ce n'est pas une vérité stylistique et le rapport indique le seuil réellement utilisé pour chaque section.

**Jeu d'essai réel anonymisé — à fournir par le réalisateur, relu avant publication.** Je n'ai accès à aucun vrai projet ; je ne l'invente pas. Outil : `python -m kolnoa_editor.experimental.memory_proto anonymize <dossier de session ou timeline.json> sortie.json [--deny "nom1,nom2"] [--chapters-map f.json] [--force]`. Le fichier produit n'entre dans `tests/data/anonymized/` qu'après relecture humaine et ajout d'un `AUDIT.md`. `analyze` produit le rapport.

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

Pour apprendre « une logique de sélection indépendante du numéro de caméra », le réalisateur affecte **une fois par tournage** un rôle à chaque angle (ex. `large`, `moyen`, `serré`, `drone`, `réactions`, `plan fixe`). Le profil ne parle que de rôles. Un rôle non renseigné reste « inconnu » : il n'est jamais deviné. Pour ne pas redemander la même saisie : **préréglages de rôles** par type de tournage, reprise des affectations du tournage précédent *proposée à valider*, import depuis les étiquettes existantes. L'inférence automatique de la taille de plan est **expérimentale** et ne sera proposée qu'après un rapport sur vrais rushes.

### 2.3 Séquences : posées par l'humain d'abord

La détection automatique de grandes séquences est un problème ouvert et non vérifié. v1 : les sections sont des **repères `chapter`** posés par le réalisateur (ou déduits des plages d'un fichier de sections). La détection automatique viendra en proposition à valider, étiquetée avec sa confiance. Pour éviter la ressaisie : **modèles de chapitres** (ex. mairie → cérémonie → cocktail → réception → danses), import des repères existants. **Règle si un chapitre est déplacé** : la section suit ses bornes ; comme un repère `Marker` ne suit pas les opérations, les chapitres seront posés comme **repères liés au contenu** (ancres de la PR #25) et jamais comme repères de temps fixe : sinon l'analyse deviendrait fausse en silence.

### 2.4 Apprendre des corrections (§4)

Source : le **journal de session** de la proposition générée. Les opérations issues de la proposition sont étiquetées (PR #25 E/G) ; toutes les opérations manuelles qui suivent sont comparées à l'état proposé et classées en types d'écart (plan prolongé, plan de coupe raccourci, angle remplacé, transition supprimée, plan réintégré…).

**Seuils de départ (décidés par le réalisateur, configurables, à tester — pas des valeurs scientifiquement établies).** Ils servent **uniquement à décider quand proposer une règle** ; ils n'autorisent **jamais** une promotion automatique.

| Niveau | Condition pour **proposer** au réalisateur |
| --- | --- |
| Observation | une correction validée crée une observation *en attente* |
| Préférence de séquence | ≥ 3 corrections similaires dans ≥ 2 projets indépendants, **même type de section** ; puis validation explicite |
| Préférence générale | ≥ 8 corrections similaires dans ≥ 4 projets indépendants et ≥ 2 types de sections ; puis validation explicite |
| Contradiction | promotion **bloquée** ; arbitrage demandé |
| Cause technique | exclue de l'apprentissage artistique (angle instable, non synchronisé, média défectueux, confiance faible) |

Règles de comptage : des corrections **corrélées d'un même montage ne sont pas des preuves indépendantes** (dix corrections identiques dans un seul mariage = 1 projet : jamais une règle générale). « Corrections similaires » = même type d'écart, même rôle d'angle ou catégorie de plan, même type de section ; la similarité est définie et testée, pas laissée au jugement du modèle.
Une observation « ponctuelle » reste valable pour le projet seulement. Le panneau « Ce que Kolnoa Editor a appris » liste candidates et preuves ; accepter / modifier / rejeter est le seul chemin ; retour arrière par version.

### 2.5 Génération et explicabilité

Le moteur applique le profil en **contraintes et préférences** sur le générateur de premier montage (préparation multicam, plans de coupe, transitions), jamais en copiant des images. Chaque décision de la proposition porte la règle qui l'a produite (`R012`) ; le rapport avant application affiche : structure et durée par partie, décisions multicam principales, catégories de plans de coupe, transitions, éléments du film de référence réellement reproduits (règles utilisées), incertitudes et séquences absentes. Actions : prévisualiser, appliquer (**une** transaction réversible), modifier les consignes, annuler. Les séquences absentes du nouveau tournage ne sont **pas** inventées : elles sont listées comme « non trouvées ».

### 2.6 Bibliothèque intelligente de plans de coupe (PR « L », avant M6)

Décision du réalisateur : PR dédiée **avant M6** (génération et apprentissage des plans de coupe). Le chutier `Subclip + tags` est un point de départ, pas la bibliothèque. Besoin : indexer des **plages temporelles dans les rushes**, distinguer les types de plans, noter leur qualité technique, retrouver un plan par rôle narratif — et renvoyer des **candidats avec timecodes**, pas « une vidéo étiquetée alliances ».

```
ShotSegment                           # entité de projet, journalisée et annulable comme le reste ; n'écrit jamais le média
  id, media (identifiant de média stable, pas seulement le chemin), src_in_frame, src_out_frame, src_fps (rationnel)
  kind          # type de plan : détail, plan d'ambiance, réaction, large, insert, drone… (taxonomie extensible)
  labels[]      # {label, origin: manuel | automatique, confidence 0..1, by, at}      ← distinction exigée
  roles[]       # rôle narratif : {role: masquer_un_raccord | émotion | illustration | transition…, origin, confidence}
  quality{}     # {metric: stabilité | netteté | exposition | mouvement_brusque, value, origin, confidence, verified: bool}
  notes, status # actif | à revoir | exclu
Query (structurée)
  subject, kind, roles, min_duration_s, max_motion, stable_only, exclude_used, near (ancre de timeline), must_cover (plage à masquer)
Candidate
  segment, sub_range [in, out] en images source, score, raisons[] (quelle étiquette/mesure a compté), confidence, verified: bool
```

Exemple : *« un joli plan des alliances, stable, sans mouvement brusque, pour masquer cette coupe »* → l'assistant (PR #25 F) le convertit en `Query` (sujet « alliances », stabilité, `must_cover` = plage ancrée de la coupe) ; la bibliothèque renvoie des **candidats avec timecodes** (plage source exacte, assez longue pour couvrir la coupe, avec marge de fondu), classés, avec leurs raisons. **Jamais d'insertion automatique** : le candidat devient une intention `Cutaway` (PR #25 E) à valider.
Deux niveaux : (1) **manuel/indexé** d'abord (I/O dans le chutier, catégories, rôles, qualité saisie) — suffisant et vérifiable ; (2) **analyse visuelle expérimentale** (stabilité, mouvement brusque, netteté) proposée avec `verified = false` et sa confiance, après rapport en lecture seule sur de vrais rushes. Une étiquette automatique ne remplace jamais une étiquette manuelle ; en cas de désaccord, la manuelle l'emporte et le désaccord est signalé. La bibliothèque ne bloque pas M1–M5.

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

*Honnêteté statistique (révisée après revue).* Un seul film de référence **ne valide pas la généralisation** : avec un seul projet, tout résultat est étiqueté **exploratoire / non concluant**. Les plans d'un même film ou d'une même séquence sont corrélés ; un *bootstrap par plans* donnerait une fausse précision. **L'unité indépendante est le projet** ; à défaut, des **blocs de sections** avec limites signalées. Les intervalles ne sont donnés qu'à partir de plusieurs projets.

*Fixé avant les essais* : seuils de succès **et d'échec** par métrique (écrits dans le protocole, pas ajustés après coup) ; **B0 et B1 sur des rushes strictement identiques** ; aucune fuite entre apprentissage et évaluation (le film évalué est exclu du profil ; empreintes de contenu vérifiées). **Métrique principale en usage réel : temps de correction en minutes humaines** (chronométré), en plus du nombre d'opérations et d'images modifiées ; contrepoids : **qualité perçue en aveugle** et **respect des moments obligatoires** (aucun moment marqué obligatoire omis).

## 4. Découpage en PR

Chaque PR : indépendante, en brouillon, tests automatisés, documentation, critères de validation. Aucune fusion sans accord.

| PR | Contenu | Tests | Critères de validation | Dépend de |
| --- | --- | --- | --- | --- |
| **M0 (cette PR)** | Plan révisé + prototype **expérimental isolé** `experimental/memory_proto/` (lecture seule) + outil d'anonymisation | 31 tests : valeurs exactes, cadences 24/25/30/50, arrondis, trous, chevauchements, fondus, annulation par `Session`, pistes secondaires, anonymisation, isolation (aucun import depuis la production) | métriques documentées (§1 bis) ; un jeu d'essai **réel anonymisé** fourni par le réalisateur passe l'analyse ; aucune écriture dans la timeline | — |
| **M1 — Analyseur de référence** | **réimplémentation propre** des parties du prototype retenues après validation sur cas réels (le prototype reste l'oracle de tests) : chapitres ancrés, rôles d'angle, seuils réglables, rapport lisible, CLI `profile-analyze`, rapport sur vrais projets (lecture seule, comme `sync-calibrate`) | propriétés (invariance au déplacement global, additivité) ; projets incomplets, médias manquants, sections absentes | résultats exacts sur cas construits à la main ; rapport sur un vrai projet relu par le réalisateur | M0 |
| **M2 — Gestionnaire de profils** | `StyleProfile`, règles, versions, dupliquer / exporter / importer / supprimer, combinaison par section (« structure de A, rythme des danses de B ») ; **profil privé local** (provenance complète) distinct du **profil portable anonymisé** ; contrôle d'export (*lint*) | aller-retour JSON ; combinaison déterministe ; retour à une version ; le *lint* échoue si `sources`, `examples`, `provenance`, identifiants ou empreintes contiennent un nom de client/fichier | aucune perte à l'export/import ; aucun client identifiable dans un profil portable | M1 |
| **M3 — Rôles d'angle** | affectation par tournage (jamais devinée), persistée, utilisée par l'analyseur | rôle absent ⇒ « inconnu » ; migration | jamais d'inférence silencieuse | M1 |
| **M4 — Observations et panneau « appris »** | modèle `Observation`, états, provenance, retour arrière, panneau (logique sans écran + UI) | une correction isolée ne change jamais le profil ; accepter/rejeter/modifier ; versionnage | aucune règle ne naît sans décision explicite | M2 |
| **M5 — Apprentissage des corrections** | comparateur proposé ↔ validé sur le journal, classification des écarts, portée ponctuelle / section / générale, étiquette de cause technique | écarts construits à la main ; causes techniques non promues en préférences | scénarios d'acceptation 5 et 6 | M4, PR #25 (E, G) |
| **L — Bibliothèque intelligente de plans de coupe** | `ShotSegment`, `Query`, `Candidate` (§2.6), manuel/indexé d'abord, étiquettes manuelles vs automatiques avec confiance, qualité technique, rôles narratifs, recherche de candidats avec timecodes, intégration à `Cutaway` ; niveau visuel expérimental (`verified=false`) | exactitude des plages (images source ↔ timeline, 25/50 i/s) ; une étiquette manuelle l'emporte ; recherche : sujet + stabilité + durée suffisante pour couvrir une coupe ; rushes inchangés | l'exemple « alliances, stable, sans mouvement brusque, pour masquer cette coupe » renvoie des candidats avec timecodes et raisons, sans insertion automatique | A2, C de #25 |
| **M6 — Générateur sensible au style** | profil → contraintes/préférences du premier montage : structure, multicam (plans stables, rôles), plans de coupe par catégorie, transitions | validité (0 refus), déterminisme, respect des règles dures, séquences absentes listées | scénarios 2, 3, 4 ; comparaison B0/B1 produite | M2, M3, **L**, PR #25 E |
| **M7 — Contexte de prompt** | références à d'autres projets dans les consignes (« comme le film A, danses du film B ») | résolution de références ambiguës ⇒ question | scénario 7 | M2, PR #25 F |
| **M8 — Validation et explicabilité** | rapport avant application, explication par règle, Appliquer/Annuler en une transaction | un seul pas d'undo ; annuler = état antérieur identique | scénario 8 | M6, PR #25 G |
| **M9 — Banc de comparaison** | harnais du §3 : B0/B1/H, métriques, rapport avec intervalles, présentation en aveugle | métriques sur cas connus ; bootstrap reproductible | protocole exécutable de bout en bout sur données synthétiques | M6 |
| **M10 — Robustesse** | médias manquants, projets incomplets, profil sans accès au film de référence, rushes intacts | scénarios 9 et 10 | empreintes inchangées | M6 |

**Exigence transversale (revue)** : la mémoire est **au service du mode mission** (« mission complète en langage naturel → autonomie → proposition → finitions », voir `PROMPTS_ASSISTANT.md` §13), pas une interface de configuration obligatoire : un profil par défaut est choisi/proposé automatiquement et chaque réglage reste facultatif.

Dépendances externes : transitions vidéo (PR K de #25), détection de séquences, analyse de stabilité (PR I de #25).

## 5. Risques et décisions demandées

- **Peu de données** : un film = un échantillon ; les profils doivent afficher `n` et leur confiance, et rester des *guides*, pas des lois.
- **Préférence ou contrainte ?** Un plan évité parce que le rush est défectueux n'est pas un goût ; la cause technique est enregistrée avec l'observation.
- **Films exportés seuls** : n'apportent que la structure grossière et les durées ; pas d'angles ni de plans de coupe.
- **Respect de la vie privée** : le profil ne contient ni média, ni nom de fichier source, ni nom de client ; il est exportable.

**Décisions prises** (réalisateur, revue ChatGPT) : sections et rôles posés par l'humain en v1 avec modèles et préréglages ; bibliothèque de plans de coupe en PR dédiée avant M6 ; seuils de §2.4 comme valeurs de départ configurables, sans promotion automatique ; prototype conservé comme outil expérimental isolé ; un film de référence = résultat exploratoire.
**Restent à décider** : fournir un projet réel anonymisé pour le jeu d'essai ; format du *lint* d'export (quelles zones vérifier) ; valeur du seuil « respire » (6 s par défaut) ; quelles parties du prototype réimplémenter en M1.
