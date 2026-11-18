# Première bêta Windows — état des lieux, blocages et plan (révision du 2026-11-18)

Remplace la version du 17/11, que ce relevé corrige sur un point important (§1). Relevé fait sur le **sommet de la pile (#29)** et par simulation de fusion locale ; aucun code produit écrit depuis. Budget additionnel : **0 € définitif** ; la passerelle de l'issue #30 est abandonnée (PR #31 fermée).

## 1. Le constat qui change le plan
**Il n'existe pas encore d'application.** Ce qui existe : un **moteur** solide (timeline, opérations réversibles, multicam, synchro, audio, ancres) piloté en **ligne de commande** (`kolnoa …`, 27 commandes), plus une **fenêtre d'essai** (`ui/demo_app.py`, 243 lignes : « choisis 2 à 9 vidéos », proxys de 90 s, timeline + lecteur multicam pour *mesurer le ressenti*). Il manque la **coquille d'application** : ouvrir/créer un projet, importer, synchroniser, monter, enregistrer, exporter — le tout depuis une fenêtre. Mon tableau du 17/11 notait l'interface « partielle » : c'est exact pour la timeline/le lecteur, mais **trompeur pour le produit**.

## 2. Existant, par exigence de la bêta (état au sommet de la pile #29)
Légende : ✅ fonctionne et testé (synthétique) · 🟡 partiel / sans interface / non éprouvé sur vrais rushes · ❌ absent du sommet de la pile · ⛔ bloquant.

| Exigence | État | Ce qui existe / ce qui manque |
|---|---|---|
| Importer rushes Sony A7 IV 4K50 + audio | 🟡 | `scan` : détection A7IV (dossier/nom de fichier), fichiers audio, empreintes, incrémental, manquants ; `proxies` 540p reprenables. **Aucune interface d'import**. Jamais essayé sur de vrais 4K50. |
| Synchroniser plusieurs caméras | 🟡 | PR #9 : synchro audio, dérive, discontinuités, alignement manuel, drone muet (expérimental). CLI seulement ; **seuils non calibrés sur vrais rushes** ; aucune interface de synchro. |
| Monter en multicam | 🟡 | Touches 1–9, enregistrement de prises, lecteur parallèle (#14/#19), source audio maître, dual mono (#16). Dans la fenêtre d'essai ; **budget de décodage 4K50/GPU jamais mesuré** (fichiers `MESURES.md`/`mesures.json` jamais reçus). |
| Sélectionner, couper, déplacer, raccourcir | 🟡 | Moteur complet (rasoir, liens, insert/overwrite, ripple, roll, magnétisme). Interface : déplacer, rogner, rasoir (S), glisser depuis le chutier. **Pas essayé par le réalisateur.** |
| Raccourcis, annuler/rétablir | 🟡 | Raccourcis configurables ; annuler/rétablir **persistants** dans le moteur (journal). |
| Musique | 🟡 | Plan sur piste audio + mixage mesuré (`audio-mix`, WAV). **Pas dans le rendu MP4** ; champ historique `Timeline.music` non mixé. |
| Plans de coupe | 🟡 | Posables sur une 2ᵉ piste vidéo ; **non rendus** (un seul flux vidéo exporté) ; pas de bibliothèque. |
| Transitions | ❌ | Seuls les fondus enchaînés **audio** sont au sommet. Spécification (#6) et pose/étirement (#7) sur une branche **à l'ancienne base** : conflits à la fusion (§4). |
| Couleurs et LUT | ❌ | Modèle non destructif + rendu + CLI (#11) sur une branche à part : **conflits** avec le sommet (§4) ; aucune interface. |
| Enregistrer / rouvrir sans perdre le montage | 🟡 | Moteur : `base.json` + journal + `current.json`, annulation persistante, versions. **Aucune interface** ; la fenêtre d'essai ne sauvegarde rien. |
| Export MP4 1080p/4K | ⛔ | `render` / `timeline-render` : seulement un montage **simple** (une piste vidéo, sans multicam). Pas de bouton. |
| Assistant (consignes en langage naturel) | ❌ | Fondations prêtes : ancres A1 (#27–#29, en revue). Rien d'autre (A2 non autorisée). |
| Mémoire de réalisation | 🟡 | Prototype isolé et plan (#26) ; **aucune vraie timeline anonymisée testée**. |
| Installateur Windows simple | 🟡 | EXE d'essai (PyInstaller via workflow GitHub, gratuit en dépôt public). Pas d'installateur du produit : il n'y a pas encore de produit. |

Tests : **448 passés** au sommet (CI GitHub verte sur #27/#28/#29). Ils sont **synthétiques** : aucun vrai rush n'a jamais été utilisé.

## 3. Blocages (⛔) dans l'ordre de gravité
1. **Pas de coquille d'application** (projet, import, synchro, enregistrement, export). Plus gros chantier restant.
2. **Rendu MP4 incomplet** (multi-pistes, multicam, musique+mix). Proposition J1 déjà écrite (`docs/J1_RENDU_COMPLET_PROPOSITION.md`).
3. **Pile de 25 PR non fusionnée**, dont #11 (couleur) et #6/#7 (transitions) qui **entrent en conflit** avec le sommet.
4. **Aucune mesure ni essai sur les vrais rushes** (décodage 4K50, bascule d'angle, calibration de la synchro).
5. #3 à #9 ouvertes **hors brouillon** (héritage) — à confirmer.

## 4. Dépendances entre PR et ordre de fusion proposé (rien n'est fusionné)
Structure réelle (base → branche) : `main ← #3 ← #4 ← #5 ← #9 ← #13 ← #14 ← #16 ← #18 ← #19 ← #20 ← #21 ← #22 ← #23 ← #24 ← #25 ← #27 ← #28 ← #29`. Branches latérales : #6/#7 (transitions, sur #5), #10 (docs), #11 (couleur), #12 (tâches), sur #9 ; #15/#17 (bancs), sur #14 ; #26 (mémoire), sur #24 ; #8 (docs), sur `main`.

**Simulation de fusion dans le sommet (locale, rien poussé)** : sans conflit — #12, #10, #15, #17, #8, #26. **Avec conflits** — #11 (`timeline/__init__`, `convert`, `model`, `ops`) et #7 (les mêmes + `cli.py`).

Ordre proposé :
1. **#3 → #4 → #5 → #9 → #13 → #14 → #16 → #18 → #19 → #20 → #21 → #22 → #23 → #24**, en **commits de fusion (pas de squash)** pour que les branches empilées restent valides ; ne pas supprimer une branche avant la fusion de la suivante, puis retarget automatique.
2. Docs : #8, #10, puis #25, #26 (sans conflit).
3. Sans conflit mais utiles : #12 (tâches en arrière-plan), #15/#17 (bancs/EXE d'essai).
4. **A1 : #27 → #28 → #29** (déjà empilés sur #25).
5. **#11 (couleur) puis #6/#7 (transitions) : à rebaser sur le sommet** par moi, une PR à la fois, avec tests — conflits à résoudre dans `model`/`ops`/`convert`. Ordre conseillé : couleur d'abord (modèle isolé), transitions ensuite.
Alternative : une seule PR d'intégration du sommet vers `main` après revue globale. Je recommande l'ordre PR par PR (relecture plus fine).

## 5. Plan pour atteindre la bêta (chaque jalon = PR brouillon, **validation requise avant de commencer**)
| Jalon | Contenu | Dépend de |
|---|---|---|
| P0 | Fusions dans l'ordre §4 ; rebase #11 puis #6/#7 | décision du réalisateur |
| P1 | **Rendu complet (J1-a…d)** : aplatissement des pistes, plans de coupe, multicam, musique+mix, MP4 1080p/4K | P0 |
| P2 | **Coquille d'application** : projet (créer/ouvrir/enregistrer), import + proxys en tâche de fond, synchro (assistée), timeline/lecteur, export ; réutilise tout l'existant | P0 |
| P3 | Couleur/LUT et transitions branchées au rendu et à l'interface | P1, P2 |
| P4 | Mesures 4K50/GPU et calibration de la synchro sur vrais rushes (lecture seule) | une session groupée du réalisateur |
| P5 | Session d'essai sur vrais rushes → correctifs | P1–P4 |
| P6 | **Bêta** : installateur Windows unique (workflow gratuit), notes de version, liste des limites | P5 |
| Expérimental (parallèle, sans bloquer la bêta) | Assistant : interface de « fournisseur » avec **moteur local sans API** (analyse de consignes par règles + clarification), branchée sur les ancres A1 ; mémoire de réalisation sur une timeline anonymisée | A1 validée |

## 6. Assistant et mémoire sans API payante (architecture seulement)
Un `Provider` à contrat unique : **v1 = règles locales déterministes** (aucun réseau, aucun coût) ; un fournisseur « manuel » où la consigne est rédigée avec ChatGPT/Claude **dans leurs interfaces existantes** et importée comme fichier d'opérations (déjà possible : `timeline-apply`). Les consignes passent par `ConfirmedTarget` : aucun ciblage ambigu appliqué sans confirmation. Aucune clé, aucun service facturé.

## 7. Essais sur les vrais rushes (protocole, une seule demande groupée)
Lecture seule. Le réalisateur lance une fois l'outil de mesures (`MESURES.md`, `mesures.json`) et exporte une timeline anonymisée (outil #26, relecture humaine). Rien n'est copié ni modifié ; noms de clients exclus du dépôt public.

## 8. Décisions demandées
1. Autoriser **P0** (fusions) : oui/non et ordre.
2. Autoriser **P1** (J1-a→d) et **P2** (coquille) ; lequel d'abord ? (je recommande P1-a puis P2 en parallèle).
3. Passer #3–#9 en brouillon ?
