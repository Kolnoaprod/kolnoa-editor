# PR A1 — ancres de contenu stables : proposition technique

Statut : **proposition documentée, aucune ligne de produit écrite.** L'implémentation d'A1 ne démarre qu'après le feu vert explicite du
réalisateur et la revue de ChatGPT (PR #25 reste en brouillon, rien n'est fusionné). Ce document lève les trois points ouverts de la
revue de suivi : **`media_id`**, **performance**, **repères liés au contenu (PR R)**. Il complète `PROMPTS_ASSISTANT.md` §4.1 et §10.

## 0. Constats dans le code actuel (sur lesquels tout repose)

| Constat | Source | Conséquence |
| --- | --- | --- |
| `Clip.media` et `Angle.media` sont des **chemins relatifs** (texte) ; aucun identifiant de média | `timeline/model.py` | un fichier déplacé ou réimporté casse toute référence par chemin ; il faut un identifiant indépendant du chemin |
| `compute_quick_hash` = taille + **3 Mio échantillonnés** (début, milieu, fin) ; `SourceFile.fingerprint` = (taille, mtime) | `scanner.py` | utile pour *proposer* un rapprochement, **insuffisant comme preuve d'identité** (deux fichiers différents peuvent coïncider sur ces échantillons) |
| `Timeline.fps` est un **entier** ; secondes source arrondies à 6 décimales (`timebase`) ; aucun champ de vitesse dans `Clip` | `timeline/model.py`, `timebase.py` | 23,976 / 29,97 i/s et ralentis ne sont pas représentables : **refus explicites** (§3.4) |
| `ProbeInfo.is_vfr` existe (écart > 2 % entre cadence moyenne et de base) | `probe.py` | la détection de cadence variable est réutilisable pour refuser |
| Le multicam a un axe de temps de groupe : `t_groupe = offset + t_angle·(1 + dérive·10⁻⁶)`, et son inverse `to_angle` | `Angle` | les ancres des plans multicam se posent **sur l'axe du groupe** : stable quand on change d'angle |
| Filiation : `Clip.parent` ; ids supprimés jamais réutilisés (`retired`) ; pas de compteur de révision de la timeline | `model.py` | la filiation aide le diagnostic (`hors_plan`), mais **la vérité est le contenu source** |

## 1. Périmètre

**Dans A1** : (1) registre d'identité des médias, (2) base de temps exacte, (3) `ContentAnchor` et sa résolution typée, (4) contrat de
confirmation, (5) banc de propriétés et de performance reproductible.
**Hors A1** : consignes (A2), interface (B), repères liés (R), tout appel à un modèle d'IA. **Aucun changement du schéma de la timeline** :
`Clip.media` reste un chemin ; A1 ajoute un registre *à côté* et un module pur `kolnoa_editor/anchors/`. Aucun rush n'est lu en écriture.

Découpage proposé (trois PR empilées, chacune relisible seule — à confirmer par ChatGPT ; sinon une PR en trois commits) :
**A1-1** registre `media_id` + rapprochement ; **A1-2** ancres + résolveur + contrat de confirmation ; **A1-3** banc de propriétés (≥ 10 000 suites)
et banc de performance.

## 2. Point 1 — `media_id` et rapprochement (A1-1)

**Décision proposée** : identifiant **opaque et immuable** (UUID v4) attribué à l'enregistrement d'un média, **indépendant du chemin, du hash et
de la date**. Il ne se calcule jamais à partir du contenu.

```
MediaRegistry  (fichier `media_registry.json` dans le dossier de données du projet ; écriture atomique ; ajout seulement)
  media[]: { media_id, path_rel (indice MUTABLE), size, duration_us, fps (rationnel "num/den"), vfr (bool), kind,
             locations[] historique des chemins, status: present | manquant | remplacé,
             evidence: { size, mtime_ns, quick_hash?, confirmed_by: "auto-chemin" | "utilisateur" } }
  jamais supprimé : un média retiré passe en `manquant` (les ancres qui le visent restent lisibles)
```

**Rapprochement distinct de l'identité** (`relink`) — un média déplacé/réimporté n'a pas un nouvel `media_id` *par défaut* :

| Situation | Décision |
| --- | --- |
| même chemin, même taille et mtime | **certain** : même `media_id` (preuve d'usage, pas de calcul) |
| chemin différent, **un seul** candidat avec même taille + durée + cadence + `quick_hash` | **probable** : proposé à l'utilisateur, jamais appliqué seul |
| plusieurs candidats, ou signaux contradictoires (même `quick_hash`, durée différente…), ou fichier remplacé (même chemin, contenu différent) | **ambigu / collision** : choix humain obligatoire ; les ancres restent `perdue` en attendant |
| confirmation humaine | enregistrée (`confirmed_by: utilisateur`) ; ajoute le chemin à `locations` ; la timeline est mise à jour par une **opération annulable** (`SetClipProps.media`), pas par A1 |

Règles de sécurité : un hash **partiel n'est jamais une preuve d'identité** (il ne sert qu'à proposer) ; un hash complet optionnel (vérification
lente, à la demande) peut passer une proposition de « probable » à « vérifié » mais ne dispense pas de la confirmation en cas de doublon ;
**aucune empreinte privée** (`quick_hash`, taille, mtime, chemins) n'est écrite dans un profil portable ni dans un export (le contrôle d'export de #26
refuse ces clés) ; un `media_id` exporté est remplacé par un indice local.

Tests A1-1 : attribution stable après redémarrage ; déplacement certain/probable/ambigu ; deux fichiers identiques sur échantillons mais différents
(collision simulée) ⇒ jamais fusionnés silencieusement ; fichier remplacé au même chemin ⇒ nouveau `media_id`, ancre de l'ancien `perdue` ;
média disparu ⇒ `manquant`, ancres lisibles ; écriture atomique (crash simulé) ; aucun écrit sur les rushes (empreintes avant/après).

## 3. Points techniques d'A1-2

### 3.1 Base de temps exacte

Temps source canonique : **entier en microsecondes** (`src_us`), cohérent avec l'arrondi à 6 décimales déjà utilisé par `timebase`.
Toute l'arithmétique d'intervalle est **entière** (`src_in_us = round(src_in·10⁶)`) ; la conversion vers l'image de timeline est
`frame = start + round((src_us − src_in_us)·fps / 10⁶)` et la conversion inverse `src_us = src_in_us + round((frame − start)·10⁶ / fps)`.
Pour tout `fps ≤ 1000`, l'erreur d'aller-retour est < 0,001 image : le **test exhaustif** vérifie que *chaque* image de chaque plan revient à
elle-même à 24, 25, 30, 50 et 60 i/s, avec décalage de départ, `src_in` fractionnaire et, en multicam, dérive jusqu'à ±1 000 ppm.

### 3.2 Structure d'une ancre

```
ContentAnchor (immuable)
  id                    # stable, jamais réutilisé
  media_id | group_id   # un plan simple vise un média ; un plan multicam vise l'AXE DU GROUPE (stable au changement d'angle)
  src_us                # position exacte (µs) — média ou axe du groupe
  src_frame, src_fps    # image source et cadence rationnelle, pour affichage et contrôle de cohérence (refus si incohérent)
  hints: clip_id, root_clip_id (filiation), angle_id, frame_hint, sequence_id, created_at   # INDICES, jamais une vérité
  neighbors_sig         # signature facultative du voisinage (plans voisins, racines de filiation) pour `contexte_modifié`
Selection (cible d'une consigne)
  Point(anchor…)            une ancre par plan présent sous la position (piste vidéo principale = primaire)
  Range(segments[])         COLLECTION de segments [src_us_in, src_us_out) résolus plan par plan — jamais deux ancres extrêmes
  Clips(anchors[])
```

### 3.3 Résolution typée et sans devinette

`resolve(tl, registry, selection) -> Resolution` — fonction **pure** (aucune écriture, aucun état caché).

| État | Condition | Ce que le logiciel fait |
| --- | --- | --- |
| `exacte` | exactement un plan contient le contenu | seul cas où la cible est confirmée sans question |
| `multiple` | le même contenu apparaît dans plusieurs plans (reprise, teaser, plan de coupe répété) | liste les occurrences ; **choix de l'utilisateur** |
| `hors_plan` | le contenu a été raccourci hors de la timeline mais le plan (filiation) existe | indique le bord le plus proche **à titre d'information** ; confirmation |
| `fragmentée` | une plage n'est que partiellement présente, ou ses morceaux ne sont plus contigus/dans l'ordre | liste les segments **présents** et les trous ; la cible n'est **jamais élargie** à l'enveloppe |
| `perdue` | plus aucun plan, ou média sans `media_id` valide / manquant | consigne orpheline conservée, jamais supprimée |
| `contexte_modifié` | consigne relative au voisinage et `neighbors_sig` a changé | avertissement ; confirmation |
| `non_supportée` | VFR, cadence non entière, vitesse ≠ 1 (voir §3.4) | refus explicite avec la raison |

**Contrat de confirmation (non contournable)** : seule une `ConfirmedTarget` est acceptée par les couches suivantes (A2/E). Elle ne peut être
construite que par `Resolution.confirm()` : automatiquement si `exacte`, sinon avec un **choix explicite** de l'utilisateur
(`ConfirmationChoice` : occurrence retenue, segments retenus). Un test de conception échoue si une API publique d'A2/E accepte autre chose qu'une
`ConfirmedTarget`. L'ancre garantit la stabilité du ciblage, pas la compréhension sémantique du prompt : l'IA propose, la compilation valide,
le réalisateur approuve.

Algorithme : un **index par média/groupe** (intervalles `[début, fin)` en µs triés par début, avec maximum de fin cumulé) donne les plans
contenant un point en O(log n + k) ; l'index est reconstruit seulement quand la timeline change (clé = position du journal de session, ou à défaut une
somme de contrôle O(n) des plans) ; une plage se résout en balayant les intervalles qu'elle coupe.

### 3.4 Refus explicites en V1 (jamais d'arrondi silencieux)

- **Source à cadence variable** (`ProbeInfo.is_vfr`) : création d'ancre refusée ; ancres existantes ⇒ `non_supportée`.
- **Timeline à 23,976 / 29,97 i/s** : `fps` est entier ; une timeline non entière est refusée au chargement par A1 avec message clair (traitement futur lié à une évolution du modèle).
- **Retiming / ralenti** : aucun champ de vitesse n'existe ; si `Clip.extra` contient une vitesse ≠ 1 (futur), le plan est `non_supportée`.
- Une cadence source ≠ cadence timeline (50p sur 25) est **supportée** : on résout en µs, pas en images.

## 4. Banc de propriétés et de performance (A1-3)

### 4.1 Exactitude : « 0 résolution fausse silencieuse »

Oracle **indépendant** : pour chaque suite aléatoire d'opérations du moteur (déplacer, scinder au rasoir, raccourcir, ripple, supprimer une plage,
placer/remplacer, changer d'angle, joindre, annuler/rétablir par `Session`), on étiquette chaque image de chaque plan par son contenu source et on
calcule par **balayage exhaustif** où se trouve chaque contenu. Critère par ancre : si le résolveur répond `exacte`, le balayage confirme **une seule**
occurrence à cette image ; sinon, le balayage confirme la condition de l'état annoncé (plusieurs occurrences, partielle, absente…). Toute divergence = échec.
≥ 10 000 suites (graine fixe affichée, rejouable), plus les scénarios nommés : média réimporté/déplacé, mêmes plages réutilisées, changement
d'angle multicam avec dérive, filiation, annulation/reprise, autre séquence (le même contenu dans un teaser), VFR/29,97/ralenti refusés, suppression
d'un plan intermédiaire d'une plage (la cible ne s'élargit pas).

### 4.2 Performance : une hypothèse à mesurer, pas un engagement

« p95 < 5 ms pour résoudre toutes les consignes » reste **l'hypothèse de départ**. Un SLA n'est retenu qu'après mesure. Banc reproductible
(`tools/bench/bench_anchors.py`, rapport JSON + Markdown) :

| Axe | Valeurs |
| --- | --- |
| Taille du projet | petit 200 plans / 20 ancres ; moyen 2 000 / 200 ; gros 10 000 / 1 000 ; extrême 50 000 / 5 000 |
| Scénarios d'édition | après déplacements, après suppression de 10 % des plans en ripple, après des milliers de scissions, multicam avec 9 angles et dérive |
| Régimes | **à froid** (construction de l'index + toutes les résolutions) et **à chaud** (index en cache, une consigne, puis toutes) |
| Statistiques | p50 / p95 / p99, nombre d'itérations, graine, **machine** (CPU, Python, OS), version du code |

Le rapport est produit dans l'environnement de test et, séparément, sur le PC de montage du réalisateur (comme pour les mesures 4K50) : les chiffres
de l'un ne valent pas pour l'autre.

## 5. Point 3 — repères « liés au contenu » (PR R, rappel de cadrage)

- Le `Marker` historique garde **strictement** sa sémantique de **temps fixe** : aucun projet existant ne change de comportement.
- Un repère « lié au contenu » est un objet distinct qui **porte un état de résolution** (`exacte`, `multiple`, `fragmentée`, `perdue`, `non_supportée`) hérité
  du résolveur, **jamais effacé silencieusement** ; son interface affiche l'état et propose la validation (choisir l'occurrence, accepter un fragment,
  laisser orphelin). Les chapitres de la mémoire de réalisation (#26) utilisent cette nature.
- R dépend d'A1 et ne commence qu'après lui.

## 6. Critères de validation d'A1 (tous mesurés, aucun seulement documenté)

1. Aller-retour image ↔ µs exact pour **toutes** les images testées (24/25/30/50/60 i/s ; décalages ; dérive).
2. ≥ 10 000 suites aléatoires, **0** divergence avec l'oracle ; graine rejouable.
3. Aucune application possible sans `ConfirmedTarget` (test de conception) ; tous les états non `exacte` exigent un choix.
4. VFR / 23,976 / 29,97 / ralenti refusés avec message, jamais approchés.
5. Registre : jamais de fusion silencieuse sur hash partiel ; empreintes privées absentes de tout export.
6. Rapport de performance (p50/p95/p99, froid/chaud, machine) publié ; SLA retenu **après** lecture du rapport.
7. Aucun rush écrit (empreintes avant/après) ; aucun changement de schéma de la timeline ; tests existants inchangés.

## 7. Questions pour la revue

- Trois PR empilées (A1-1/2/3) ou une seule en trois commits ?
- Emplacement du registre : dossier de données du projet (proposé) ou dans la session ?
- Un hash complet optionnel (lent) pour passer « probable » à « vérifié » : utile ou superflu ?
- `Resolution` pour une plage fragmentée : liste des segments présents suffisante, ou prévoir aussi une proposition de réparation (à valider) ?
