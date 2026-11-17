# Première bêta Windows — tableau d'avancement et feuille de route

**Objectif** : monter un vrai reportage de bout en bout (timeline, multicam, assistance, plans de coupe, musique, export MP4) **sans installer de versions intermédiaires** : une seule bêta, quand le parcours est complet.
Établi le 2026-11-17 d'après le dépôt, le README et les PR ouvertes. Rien de ce qui suit n'est une promesse de date.

## 0. Constat qui commande tout
`main` ne contient que le **socle** (scan, proxys, liste de décisions, rendu). **Toutes les autres briques vivent dans des PR empilées, non fusionnées** (#5 → #9 → #13/#14 → #16 → #18–#24 …). « Opérationnel » ci-dessous veut dire : implémenté et testé sur sa branche, **pas** livrable tant que la pile n'est pas fusionnée dans l'ordre sur décision du réalisateur. À noter : les PR #3 à #9 sont ouvertes **hors brouillon** (héritage d'avant la règle « tout en brouillon ») ; je n'y ai pas touché.

## 1. Tableau (✅ opérationnel · 🟡 partiel · ❌ manquant · ⛔ bloquant pour la bêta)

| Domaine | État | Détail |
|---|---|---|
| Scan, proxys, fichiers manquants, `check` avant rendu | ✅ | sur `main` |
| Rendu MP4 d'un montage **simple** (une piste vidéo continue), atomique, reprenable, raccords à l'image | ✅ | `render`, `timeline-render` |
| Timeline native, opérations réversibles, annuler/rétablir persistants | ✅ | PR #5 (non fusionnée) |
| Édition : rasoir, liens, insertion/recouvrement, ripple, roll, magnétisme | ✅ | PR #13 |
| Interface Qt : timeline, rognage, rasoir clavier, glisser depuis le chutier, formes d'onde asynchrones | 🟡 | PR #18, #20–#23 ; **jamais essayée par le réalisateur sur de vrais rushes** |
| Multicam : synchro audio, dérive, discontinuités, alignement manuel | 🟡 | PR #9 ; seuils **à calibrer sur de vrais rushes** ; drone muet = visuel expérimental |
| Multicam : lecteur parallèle 1–9, enregistrement de prises en direct | 🟡 | PR #14, #19 ; budget 4K50/GPU **non mesuré** (⛔ fichiers `MESURES.md` / `mesures.json` jamais reçus) |
| Moteur audio : dual mono, fondus, source maître, mixage mesuré | ✅ | PR #16 ; rendu du mix en fichier audio seulement |
| Musique : une piste musique, fondu de sortie, gain, rendu dans le MP4 simple | 🟡 | liste de décisions ; pas de musique multi-pistes ni de ducking |
| **Export MP4 d'une timeline complète** (multi-pistes, multicam, plans de coupe superposés, musique, mix) | ⛔ | le rendu ne sait faire que le montage simple ; « rendu multicam non disponible avant la PR D » |
| Plans de coupe (cut-aways) : placement, bibliothèque, proposition | ❌ | PR L (non autorisée) ; manuellement possible sur une 2ᵉ piste mais **non rendu** |
| Transitions vidéo, étalonnage/LUT | 🟡 | spec PR #6/#7, couleur PR #11 ; fondus enchaînés **audio** seulement dans le moteur ; PR K absente |
| Assistance au montage par prompts (directives, clarification, correction) | ❌ | proposition PR #25 ; fondations A1 prêtes (#27–#29), A2 non autorisée |
| Mission autonome (« mission complète → montage candidat → validation ») | ❌ | I1/I2 de la proposition |
| Mémoire de réalisation (style) | 🟡 | prototype isolé, PR #26 ; **aucune vraie timeline anonymisée testée** |
| Ancres de contenu stables (A1) | ✅ | #27–#29, en revue ; non branchées à l'UI ni au moteur |
| Fichier de session fiable (reprise, sauvegarde) | ✅ | base + journal ; une session par projet |
| Application d'essai Windows (EXE) | 🟡 | PR #24 ; mesures GPU en attente |

## 2. Bloquants (⛔) pour « monter un vrai reportage »
1. **Rendu de la timeline complète** : multi-pistes (plans de coupe en superposition), multicam, musique + mix dans un seul MP4. Sans lui, aucun reportage ne s'exporte.
2. **Pile de PR non fusionnée** : décision du réalisateur sur l'ordre (#9 → #13 → #14 → #16 → #18 → #19 → #20 → #21 → #22 → #23 → #24, puis rebase de #11/#12).
3. **Mesures sur la vraie machine** (RTX A4500, rushes 4K50) : décodage, bascule d'angle, affichage ; et calibration de la synchro sur de vrais rushes.
4. **Essai réel de l'interface** par le réalisateur (une seule session de test regroupée, voir §4).

## 3. Feuille de route proposée (rien n'est lancé sans validation)
Ordre = chemin le plus court vers un reportage monté de bout en bout ; l'assistance vient **après** un export fiable.

| Jalon | Contenu | Dépend de | Autorisé ? |
|---|---|---|---|
| J0 | Revue/fusion de la pile existante (ordre ci-dessus) + A1 | décision du réalisateur | en cours |
| J1 | **Rendu timeline complète** : pistes superposées (plans de coupe), multicam au rendu, musique + mix, MP4 final ; tests de non-régression « raccords à l'image » | J0 | **à valider** |
| J2 | Mesures GPU/4K50 et calibration synchro (lecture seule, sur la machine du réalisateur) | fichiers de mesure | à valider |
| J3 | Transitions vidéo simples + étalonnage de base dans le rendu (PR K réduite) | J1 | à valider |
| J4 | Plans de coupe : pose manuelle fiable + bibliothèque (PR L) | J1 | à valider |
| J5 | Assistance : directives ancrées sur A1 (A2), clarification, correction ; réglages assistants (S) | A1, J1 | à valider |
| J6 | Mission autonome (I1/I2) : montage candidat à valider | J5 | à valider |
| J7 | Mémoire de réalisation (M1…) avec une vraie timeline anonymisée | exemple du réalisateur | à valider |
| J8 | **Bêta unique** : un seul installateur, session de test regroupée, correction, puis diffusion | J1–J4 minimum | — |

Bêta « montage assisté minimal » = J0 + J1 + J2 + J3 + J4. Les jalons J5–J7 peuvent suivre sans bloquer un premier reportage monté à la main avec aides.

## 4. Pour éviter les installations intermédiaires
- Tout est testé ici en Linux (suite complète : 448 tests, CI GitHub) ; **aucune installation demandée** avant J8.
- Une seule demande à la machine du réalisateur, regroupée : lancer les mesures (`MESURES.md`, `mesures.json`) et exporter une timeline anonymisée avec l'outil dédié.
- Les chiffres de performance issus du bac à sable (2 cœurs, sans GPU) ne valent pas pour la machine de montage.
