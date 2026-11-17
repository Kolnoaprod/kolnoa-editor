# Assistant de montage par prompts — proposition technique

Statut : **proposition à valider** (aucun code dans cette PR, rien n'est fusionné). Elle prolonge l'architecture v2 (§1, §10 de
`ARCHITECTURE_V2.md`) : l'assistant n'est pas un chatbot posé à côté du logiciel, c'est **une troisième manière de produire les
mêmes opérations que la souris et le clavier**, avec des consignes attachées au *contenu* de la timeline.

Principe : *l'IA est l'assistant monteur, le réalisateur décide.* Elle propose, le moteur valide, l'utilisateur accepte ou refuse.

## 1. Règles non négociables

1. Les rushes originaux ne sont jamais écrits. Aucune vidéo n'est envoyée à un fournisseur d'IA ; seul un texte compact (voir §6) l'est, avec consentement explicite par projet.
2. Tout changement de la timeline passe par une opération du moteur (`Session.do`) : annulable, journalisé, soumis aux verrous. L'assistant ne peut rien faire qu'un outil manuel ne puisse faire.
3. Les positions de timeline sont des **images entières** (`timebase`). Une proposition est refusée si une seule de ses opérations n'est pas exacte à l'image.
4. Les analyses visuelles automatiques (stabilité, netteté, regard…) sont **consultatives et étiquetées « non vérifiées »** tant qu'elles n'ont pas été mesurées sur de vrais rushes (même démarche que `sync-calibrate`). Elles classent des candidats ; elles ne fixent jamais seules un point de coupe.
5. Rien n'est appliqué sans validation, rien n'est fusionné sans accord du réalisateur.

## 2. Ce qui existe déjà dans le dépôt

| Besoin | Déjà là | Où |
| --- | --- | --- |
| Opérations réversibles, un geste = un pas d'undo | `Op`, `Transaction`, `execute` (applique, valide, annule si invalide) | `timeline/ops.py` |
| Historique persistant, undo/redo après redémarrage | `Session` (base + journal `history.jsonl` fsync + état courant) | `timeline/session.py` |
| Outils manuels que l'IA réutilisera | `razor`, `place_clip`, `move_clips`, `replace_clip`, `delete_range`, `ripple_trim`, `roll_trim`, `trim_edge`, `ripple_delete`, `switch_angle`, `cut_to_angle`, `record_angle_switches`, `set_clip_gain`, `set_fades`, `add_crossfade` (audio), `assign_master`, `to_mono`… | `timeline/editing.py`, `ops.py`, `audio_edit.py`, `multicam_live.py` |
| Verrous de plans et de pistes | `Clip.locked`, `Track.locked`, `LockedError` | `timeline/model.py` |
| Filiation des plans (scission, `parent`) | `Clip.parent`, `Clip.link`, ids jamais réutilisés (`retired`) | `timeline/model.py` |
| Axe de temps stable pour le multicam | `MulticamGroup`, `Angle.to_angle/to_group`, `MasterRange` en temps du groupe | `timeline/model.py` |
| Synchronisation, confiance, validation humaine | `mc-sync`, `align_manual`, `accept_angle`, `lock_angle`, statuts, `sync-calibrate` | `sync/`, `cli.py` |
| Chutier et plans courts I/O | `Subclip` (média, in/out, tags, note), `place_subclip` | `timeline/chutier.py` |
| Retours rattachés au contenu, pas au temps | `comments.py` : id de segment + clip source + temps source, `_relocate` suit scissions/raccourcis, état « orphelin » | `comments.py` |
| Aperçu d'une modification sans toucher à l'état | `Timeline.scoped_copy`, `copy`, `execute` sur copie | `timeline/model.py` |
| Lecteur multicam, touches 1–9, bascules en opérations | PR #19 → #24 | `player/`, `ui/` |
| Raccourcis configurables, sélection de plans, glisser-déposer | `Shortcuts`, `TimelineView.selection`, `dnd.py` | `ui/` |
| Journalisation des envois à une IA | non | — |

## 3. Ce qui manque (écarts constatés dans le code)

- **Aucune consigne** : le champ `Clip.note` est un texte libre sans position, sans statut, sans cycle de vie.
- **`Marker` n'est pas un ancrage fiable** : il n'a que `frame`, et aucune opération (`ShiftAfter`, `move_clips`, `ripple_*`, scission) ne le déplace. Après un ripple, un repère pointe sur autre chose, sans avertissement. On ne peut donc pas l'utiliser tel quel pour les consignes.
- **Pas de marques sur les rushes** (« Obligatoire dans le teaser », « Coup de cœur », « À revoir ») : les `Subclip` du chutier s'en rapprochent mais n'ont ni nature typée, ni lien avec la timeline.
- **Une seule timeline par projet** (`_session(project)` : un dossier de session) : pas de séquence teaser indépendante.
- **Pas de transitions vidéo ni d'étalonnage dans le moteur** : seuls les fondus enchaînés audio existent ; `Clip.extra` est « réservé ». Les consignes « transition » et « couleur » ne peuvent être exécutées qu'après cette brique (PR K).
- **Pas de couche assistant** : contexte, fournisseur, schéma de propositions, aperçu, dialogue de correction, journal des échanges.
- **Pas d'orchestrateur de workflow** (import → synchro → validation des cas incertains → préparation multicam) ni de **sélection des plans stables** : rien dans le dépôt ne mesure la stabilité d'un plan.
- **Pas de modes d'interface Expert / Épuré.**
- `Timeline.fps` est un entier : le 29,97 i/s n'est pas supporté (décision à prendre, hors périmètre ici).

## 4. Modèle de données

### 4.1 Ancre de contenu (`ContentAnchor`)

Une consigne ne stocke pas « 02:13:04 ». Elle stocke **ce qu'elle désigne**, et la timeline est recalculée à partir de là
(même principe que `comments.py`, étendu au multicam et aux plages).

```
ContentAnchor
  media       chemin relatif du rush           ┐ ou, pour un plan multicam :
  group,angle id du groupe (+ angle au moment)  ┘ temps du GROUPE (stable quand on change d'angle)
  t           secondes dans le média / sur l'axe du groupe (6 décimales, via timebase)
  src_frame   n° d'image source entier + src_fps rationnel (ex. 50/1) : pour afficher et vérifier
  clip_hint   id du plan au moment de la création (indice, jamais une vérité)
  root_hint   plan racine de la filiation (suit les scissions)
  frame_hint  image de timeline au moment de la création + révision du journal (indice d'affichage)
```

**Résolution** `resolve(tl, anchor) -> Resolution` : fonction pure, sans état caché. Elle cherche dans la timeline les plans dont la
plage source (ou groupe) contient `t`, puis convertit en image de timeline : `start + round((t − src_in) · fps)`.
Résultats typés, jamais un « à peu près » :

| État | Sens | Conséquence |
| --- | --- | --- |
| `exacte` | un seul plan contient le contenu | la consigne est affichée et utilisable |
| `multiple` | le même contenu est posé plusieurs fois (reprise, teaser dans la même séquence, plan de coupe) | **ambiguë** : l'utilisateur choisit l'occurrence |
| `hors_plan` | le contenu a été raccourci hors de la timeline | ambiguë : on propose le bord le plus proche, sans l'appliquer |
| `fragmentée` | une plage a été scindée, une partie supprimée | on liste les fragments restants |
| `perdue` | plus aucun plan ne contient ce contenu | consigne « orpheline », conservée (jamais supprimée en silence) |
| `contexte_modifié` | consigne relative au voisinage (« avant le discours ») et les voisins ont changé | avertissement, pas de blocage |

Comme la position est **recalculée** et non réécrite, aucune opération existante n'a besoin de connaître les consignes : déplacer,
scinder, raccourcir, ripple, changer d'angle, annuler/rétablir restent corrects par construction. C'est le choix central de la proposition (alternative écartée : faire suivre chaque opération par une réécriture des ancres — fragile, et chaque nouvelle opération risquerait d'oublier de le faire).

### 4.2 Consigne (`Directive`)

```
Directive
  id           stable, jamais réutilisé (comme les ids de plans)
  text         formulation libre de l'utilisateur
  target       Point | Range | Clips | Sequence
               Point  : une ancre par plan sous la tête de lecture (piste vidéo principale = ancre primaire)
               Range  : ancre de début + ancre de fin (la plage peut traverser plusieurs plans)
               Clips  : liste d'ancres, une par plan sélectionné
  scope_tracks pistes concernées (par défaut : toutes celles sous la cible)
  kind         instruction | contrainte        # contrainte : « garde ce regard » reste appliquée à toutes les propositions suivantes
  status       brouillon → analysée → proposée → appliquée | rejetée | ambiguë | orpheline
  created_rev  révision du journal à la création
  proposal_ids propositions issues de cette consigne
```

Les consignes de type **contrainte** (« garde ce regard », « ne coupe pas les vœux ») ne produisent pas d'opération : elles posent une
protection que le validateur de propositions fait respecter (au même titre que les verrous), y compris pour les propositions futures.

### 4.3 Proposition (`Proposal`)

La proposition est une liste d'**intentions typées** (pas du JSON libre, pas des ids absolus de plans) :

```
Intent = SwitchAngle(target, angle) | Trim(target, edge, frames|to_frame) | Move(target, to) | Delete(target, ripple)
       | Insert(media|subclip, at, mode) | Cutaway(media|subclip, over, duration) | Gain(target, db) | Fade(...)
       | AudioMaster(target, source) | Transition(...) | Grade(...)           # les deux derniers : PR K
```

Compilation : `compile(intents, timeline) -> Transaction` appelle **exactement** les constructeurs existants (`cut_to_angle`, `ripple_edge`,
`move_clips`, `delete_range`, `place_clip`, `set_fades`…), sur une **copie** de la timeline. Le moteur valide chaque étape
(chevauchement, verrous, limites des médias source). Sortie : diff lisible (celui des `Transaction.descriptions()`), pas de résultat « à moitié valide ».

Les intentions portent des **ancres**, pas des ids : si la timeline a bougé entre l'analyse et la validation, la proposition est
**recompilée** (et le diff re-présenté s'il a changé) au lieu d'être appliquée à l'aveugle. `base_rev` enregistre la révision sur laquelle l'utilisateur a relu.

### 4.4 Marques sur les rushes (`MediaMark`)

Entité de **projet**, indépendante de toute timeline : `{id, media, t_in, t_out | point, kind, note}` avec
`kind ∈ {obligatoire_teaser, coup_de_coeur, a_revoir}`. Journalisée comme le reste (ajout/modification/suppression annulables).
Affichée dans le chutier et projetée sur chaque timeline qui utilise ce contenu (résolution par le même mécanisme qu'en 4.1).
Elle survit donc à la suppression d'un plan de la timeline principale.

### 4.5 Séquences multiples et teaser

Un projet contient plusieurs **séquences** (`principale`, `teaser`, …), chacune avec sa propre `Session` (dossier, journal, undo). Les marques
sont partagées. « Construire le teaser » = une proposition sur la séquence teaser (placement des plages marquées via `place_subclip`),
réversible, qui n'altère jamais la séquence principale.

### 4.6 Journal de l'assistant

`assistant.jsonl` (append-only, fsync, mêmes garanties que `history.jsonl`) : consignes créées/modifiées, demandes d'analyse (fournisseur, modèle,
empreinte du contexte envoyé, consentement), propositions (intentions + opérations compilées), décisions (acceptée / refusée / modifiée), lien vers
l'entrée correspondante du journal de session. Le libellé d'annulation d'une proposition acceptée est lisible : « Assistant : raccourcir 12 s — P007 ».

## 5. Les 12 exigences → mécanismes

| # | Exigence | Mécanisme |
| --- | --- | --- |
| 1 | Position / plan / sélection / plusieurs plans, ids persistants, images | `Directive.target` + `ContentAnchor` (§4.1–4.2) |
| 2 | « Ajouter un prompt ici » / « à la sélection » | commandes + raccourcis configurables (`Shortcuts`), palette de commandes ; la sélection existe déjà (`TimelineView.selection`), la plage temporelle sélectionnée est à ajouter |
| 3 | Accumuler, analyser, appliquer en lot | statut `brouillon` ; « Analyser » produit un lot de propositions ordonnées ; validation globale ou proposition par proposition |
| 4 | Mêmes opérations que les outils manuels | compilation vers les constructeurs existants uniquement (§4.3) ; PR K pour transitions vidéo et couleur |
| 5 | Prévisualisable, annulable, historisée | exécution sur copie + diff + aperçu ; acceptation = **une** `Transaction` = un pas d'undo ; journal assistant |
| 6 | Consignes fidèles au contenu, ambiguïtés signalées | résolution par contenu, états typés, aucune réécriture silencieuse (§4.1) |
| 7 | Corriger une proposition sans perdre le validé | révisions (§7) |
| 8 | Marqueurs teaser / coup de cœur / à revoir | `MediaMark` + séquence teaser (§4.4–4.5) |
| 9 | Workflow conservé | orchestrateur à étapes et points de contrôle (§8), chaque étape garde sa commande CLI actuelle |
| 10 | Originaux intacts, exactitude à l'image, analyses vérifiées | règles 1–4 ; tests d'exactitude sur toutes les opérations ; analyses étiquetées |
| 11 | Modes Expert / Épuré | couche de présentation seule, même moteur (§9) |
| 12 | Langage naturel, contexte, clarification | fournisseur d'IA + contexte compact + sortie typée ; question de clarification plutôt que supposition (§6) |

## 6. Assistant : contexte, fournisseur, clarification

- **Interface `Provider`** (Claude, OpenAI, et `FakeProvider` rejouant des réponses enregistrées pour les tests) ; clés dans le stockage sécurisé de Windows ; **aucun test n'appelle le réseau**.
- **Paquet de contexte** (texte, déterministe, plafonné) : la consigne et sa cible résolue (images, plans, pistes) ; les plans voisins ; pour chaque angle disponible à cet endroit : statut de synchro, confiance, score de stabilité *(étiqueté « non vérifié »)* ; marques, plages protégées, plages audio maître ; verrous ; consignes de type contrainte ; durées sources disponibles pour raccourcir ou rallonger. Aucun nom de fichier superflu ; aperçu exact du texte envoyé avant l'envoi, coût estimé affiché.
- **Sortie typée** : le modèle renvoie des `Intent` validées par schéma. Une sortie invalide est rejetée, pas réparée à la main.
- **Clarification** : si la résolution est ambiguë (`multiple`, `hors_plan`…) ou si l'intention est sous-déterminée (« raccourcis ce passage » sans durée), l'assistant renvoie une question avec des **choix chiffrés** (ex. « −4 s en fin de plan », « −7 s en retirant le plan 2 »), jamais une valeur inventée.
- **Les exemples du réalisateur** : « raccourcis ce passage » → `Trim`/`Delete` avec choix de durée et respect des contraintes ; « garde ce regard » → consigne *contrainte* ; « change de caméra ici » → `SwitchAngle` classé (angles stables d'abord, synchro validée seulement) ; « rends cette danse plus dynamique » → ensemble d'`Cutaway`/`SwitchAngle` et de raccourcissements sur la plage (**v1 limitée aux changements d'angle et aux coupes**, le calage sur les temps forts musicaux dépend de §8 de l'architecture v2) ; « utilise ce moment dans le teaser » → `MediaMark` + proposition sur la séquence teaser.
- Sans fournisseur configuré, toutes les opérations restent disponibles à la main : l'assistant est facultatif. Le langage naturel libre **exige** un modèle ; hors ligne il n'existe qu'un formulaire d'intentions (même schéma, sans interprétation).

## 7. Corriger une proposition sans perdre le travail validé

1. **Proposition non appliquée** : le dialogue de correction crée la *révision n+1* de la même proposition (la précédente reste consultable) ; rien n'a bougé dans la timeline.
2. **Proposition appliquée, encore au sommet de l'historique** : « Corriger » = annuler ce pas, appliquer la révision. Le travail antérieur n'est pas touché.
3. **Proposition appliquée, puis d'autres modifications** : on ne dépile **jamais** l'historique au milieu. La correction est une **nouvelle proposition compensatoire**, calculée sur l'état courant, qui reçoit en contexte les intentions de la proposition d'origine et ses opérations. « Annuler seulement celle-ci » n'est offert que si ses opérations inverses s'appliquent proprement sur l'état courant (validé sur copie) ; sinon l'interface l'explique au lieu de forcer.
4. Les modifications validées (manuelles ou issues d'autres propositions) ne sont jamais réécrites par une correction : seule la cible de la correction est recompilée.

## 8. Workflow conservé : l'orchestrateur

```
import des rushes ─▶ proxys ─▶ synchro auto ─▶ file « cas incertains » (validation humaine) ─▶ préparation multicam
   (scan/refresh)    (proxies)   (mc-sync)       accept / nudge / lock / align                   (plans stables d'abord)
                                                                                                        │
                                                          montage manuel ◀──── même moteur ────▶ consignes / assistant
```

- Chaque étape garde sa commande et ses tests actuels ; l'orchestrateur ne fait qu'enchaîner et **s'arrêter aux points de contrôle** (cas incertains non validés ⇒ pas de montage automatique sur ces angles).
- **Préparation multicam automatique** : propose un premier montage sous forme de `Transaction` (via `record_angle_switches`), en préférant les plans stables et les angles à synchro validée. Le score de stabilité (mouvement global entre images, mesuré en local sur proxys) est **expérimental** : mesuré d'abord sur de vrais rushes avec un rapport en lecture seule, comme `sync-calibrate`, avant de peser sur un choix. Il classe, il ne coupe pas.

## 9. Modes Expert / Épuré

Un `UiProfile` (préférence de l'utilisateur, hors projet) ne change que la **visibilité et la disposition** : mêmes widgets, même `Session`, mêmes raccourcis, même palette de commandes. Règles testées :
(a) une même séquence scriptée d'actions produit un journal d'opérations identique dans les deux modes ;
(b) basculer de mode en cours de montage ne perd ni la sélection, ni l'historique, ni les consignes ;
(c) toute fonction est joignable dans les deux modes (au pire par la palette de commandes) ;
(d) aucun état de projet n'est stocké dans le profil.

## 10. Découpage en Pull Requests

Chaque PR est indépendante, en brouillon, avec tests automatisés, documentation et **critères de validation**. Aucune fusion sans accord du réalisateur ; ordre de revue proposé ci-dessous.
Le socle UI actuel (#9 → #24) reste inchangé tant qu'il n'est pas validé : les PR ci-dessous s'empilent dessus, sauf A1–A2 et C1 qui n'en dépendent pas.

| PR | Contenu | Tests principaux | Critères de validation | Dépend de |
| --- | --- | --- | --- | --- |
| **A1 — Ancres de contenu** | `ContentAnchor`, `resolve()`, états typés (§4.1). Module pur, aucun changement de schéma. | Pour chaque opération du moteur (déplacer, scinder, raccourcir, ripple, supprimer, changer d'angle, copier-reprise, annuler/rétablir) : l'ancre se résout sur la bonne image ; aléatoire (propriété) sur des suites d'opérations ; fps 25 et 50 ; multicam avec dérive ; `multiple`/`perdue` détectés. | 0 résolution fausse silencieuse sur ≥ 10 000 suites aléatoires ; conversions image↔temps exactes pour toutes les images d'un plan de 1 h. | — |
| **A2 — Consignes dans le modèle** | `Directive`, schéma + migration, opérations `AddDirective`/`SetDirective`/`SetStatus`/`RemoveDirective` (annulables, journalisées), `Marker` inchangé. | Aller-retour JSON, rejeu du journal, undo/redo, migration d'un ancien projet, copie `scoped_copy` ne les perd pas. | Un projet existant s'ouvre à l'identique ; annuler/rétablir exacts ; aucune consigne supprimée par une opération sur les plans. | A1 |
| **B — Saisie des consignes (UI)** | « Ajouter un prompt ici » (tête de lecture), « … à la sélection » (plans sélectionnés ou plage), raccourcis configurables, liste des consignes, pastilles sur la timeline, signalement visuel des ambiguïtés, navigation consigne ↔ image. | Tests de logique sans écran (comme `layout.py`) ; création depuis tête de lecture / sélection ; consignes qui suivent un déplacement de plan ; états ambigus affichés. | Une consigne posée reste sur le bon plan après déplacement, scission, raccourcissement, ripple ; aucune régression des tests UI existants ; fluidité timeline mesurée comme pour #18. | A2 |
| **C — Marques sur rushes** | `MediaMark` (3 natures), opérations annulables, affichage chutier + timeline, raccourcis. | Persistance, projection sur plusieurs timelines, suppression d'un plan sans perte de la marque. | Une marque posée sur un rush est retrouvée sur toute timeline qui l'utilise ; rushes jamais écrits (test d'empreinte). | A1 |
| **D — Séquences multiples, teaser** | Plusieurs `Session` par projet, séquence « teaser » indépendante, « construire depuis les marques » (proposition réversible). | Les deux séquences évoluent sans interférer ; undo indépendants ; reconstruction du teaser depuis les marques. | Modifier le teaser ne change jamais la séquence principale (comparaison d'état) ; commandes CLI actuelles inchangées. | C |
| **E — Schéma d'intentions + compilateur** | `Intent`, validation par schéma, `compile()` vers les constructeurs existants, exécution sur copie, diff, respect des verrous et des contraintes, recompilation si la base a changé. | Chaque intention ⇔ opération manuelle équivalente (états identiques) ; refus sur verrou, chevauchement, limite source ; exactitude à l'image sur toutes les intentions ; propriété « la copie n'altère pas l'original ». | Aucune intention n'obtient un résultat qu'aucun outil manuel ne pourrait produire ; 100 % des refus du moteur remontent avec un message lisible. | A2 |
| **F — Contexte et fournisseur IA** | `Provider`, `FakeProvider`, paquet de contexte, consentement et aperçu de l'envoi, estimation de coût, clarification typée, `assistant.jsonl`. | Contexte déterministe (golden) ; sorties invalides rejetées ; clarification déclenchée sur ambiguïté ; aucun appel réseau en test ; aucune vidéo ni chemin superflu dans le contexte. | Les 5 exemples du réalisateur (§6) rejoués avec `FakeProvider` → intentions attendues ; journal complet et rejouable. | E, A1 |
| **G — Revue des propositions** | Aperçu avant/après (fantômes sur la timeline, lecture proxy), acceptation en lot ou une par une, **une** transaction par proposition, libellés d'historique, panneau d'historique du projet. | Accepter = un pas d'undo ; refuser n'écrit rien ; lot partiellement accepté ; recompilation affichée si la base a changé. | Après acceptation puis annulation, la timeline est identique bit à bit ; l'aperçu ne modifie pas l'état. | F, B |
| **H — Dialogue de correction** | Révisions de proposition, correction compensatoire, « annuler seulement celle-ci » conditionnel (§7). | Les trois cas de §7 ; travail validé intact ; refus expliqué si l'inverse ne s'applique pas proprement. | Aucune correction ne modifie un plan non ciblé ; aucun dépilage au milieu de l'historique. | G |
| **I — Orchestrateur de workflow** | Enchaînement import → proxys → synchro → cas incertains → préparation multicam, points de contrôle, file de validation ; préparation automatique via `record_angle_switches` (une transaction). Score de stabilité en **mode mesure d'abord** (rapport lecture seule). | Reprise après interruption ; arrêt sur cas incertain ; premier montage = un pas d'undo ; scores sur vidéos synthétiques. | Les commandes actuelles donnent les mêmes résultats ; **aucune annonce de précision visuelle** avant mesures sur de vrais rushes (livrable : rapport, pas une promesse). | — (s'intègre à F/G ensuite) |
| **J — Modes Expert / Épuré** | `UiProfile`, dispositions, palette de commandes complète. | Règles (a)–(d) du §9. | Journal d'opérations identique dans les deux modes pour un même scénario. | B |
| **K — Transitions vidéo et étalonnage dans le moteur** | Opérations et rendu pour transitions (fondu, dissolve, fondu au noir) et réglages d'étalonnage non destructifs ; export `convert.py` sans perte silencieuse. | Parité aperçu/export (SSIM), exactitude des durées à l'image, aller-retour du modèle. | Prérequis des intentions `Transition` et `Grade` ; sans lui, l'assistant les refuse explicitement. | — |

Ordre de revue proposé : A1 → A2 → (B, C en parallèle) → D → E → F → G → H → (I, J, K en parallèle selon les priorités). Les PR A1, C, I et K peuvent être relues indépendamment.

## 11. Risques et points ouverts

- **Contenu repris plusieurs fois** (plans de coupe, teaser dans la même séquence) : la résolution par contenu devient `multiple`. Mitigation : ambiguïté signalée + indice `clip_hint` pour proposer l'occurrence la plus probable, jamais appliquée sans choix.
- **Coût de résolution** sur de gros projets (milliers de plans, centaines de consignes) : index trié + dichotomie, comme `layout.py` ; à mesurer (objectif : résolution de toutes les consignes sous 5 ms au p95, mesuré comme pour `plan_drop`).
- **Qualité des propositions du modèle** : on ne la promet pas. On garantit le cadre : sorties typées, validées par le moteur, diff relu par l'humain, annulation en un geste.
- **Stabilité / netteté / regard** : consultatifs et non vérifiés tant que le rapport sur vrais rushes n'existe pas.
- **Confidentialité** : texte compact seulement, consentement par projet, aperçu de l'envoi, journal des envois ; dépôt public ⇒ fixtures neutres (« rush 1 »…).
- **29,97 i/s** : `fps` entier dans le modèle ; à trancher séparément.

## 12. Décisions demandées

1. Ancrage **par contenu avec recalcul** (proposé) plutôt que réécriture des ancres à chaque opération : valider ?
2. Consignes **dans la timeline** (journal de session, undo) plutôt que dans un fichier à côté : valider ? (proposé : dans la session ; le journal assistant reste à part.)
3. `MediaMark` au niveau du **projet** (partagé entre séquences) : valider ?
4. Fournisseurs : Claude et OpenAI derrière une interface commune, et **aucun** langage libre hors ligne : valider ?
5. Ordre de revue et priorité de K (transitions/couleur) par rapport à F/G.
6. Faut-il garder `Marker` tel quel (repère libre non suivi) en le documentant, ou le faire suivre les opérations dans une PR séparée ?
