# Intégration bêta — dépendances, convergence, limites, calibration

Branche `feat/beta-integration` (PR brouillon « aperçu d'intégration », **à ne pas fusionner telle quelle** : elle sert à prouver que les deux piles convergent et à porter le test commun).

## 1. Matrice de dépendances
| PR | Contenu | Base | Dépend de |
|---|---|---|---|
| #32 | J1-a aplatissement vidéo | `feat/windows-test-build` | — |
| #35 | J1-b export MP4 multi-pistes | #32 | #32 |
| #33 | provenance, garde de portée, groupes d'annulation, sélection | `feat/windows-test-build` | — |
| #34 | monteur auto v0 + contrôleur | #33 | #33 |
| #36 | coquille d'application | #34 | #34 |
| (ici) | export branché + test commun | #35 ⊕ #36 | #35, #36 |

#32 et #33 sont **parallèles** (deux racines). La fusion de #35 avec #36 a été faite sans conflit (fichiers disjoints, seul `app/controller.py` et `ui/app_window.py` reçoivent le branchement de l'export).

## 2. Stratégie de convergence (aucune fusion sans validation expresse)
1. #32 puis #35 (rendu) ; 2. #33, #34, #36 (montage/interface) ; 3. cette branche d'intégration réduite à son delta propre (export branché + test commun) ; chaque étape : suite complète + revue ChatGPT + accord du réalisateur.
Chaque branche empilée contient désormais `.github/workflows/tests.yml` (la suite complète tourne sur chaque PR — elle n'existait pas sur la lignée `windows-test-build`, d'où l'absence de résultats Actions signalée).

## 3. Test commun (`tests/test_integration_beta.py`)
Vrais fichiers ffmpeg : import → « Monte-moi cette séquence » → consigne ciblée → retouche manuelle → plan de coupe → enregistrer → rouvrir (timeline et provenance identiques) → **export MP4** → relecture : durée = timeline, son présent, couleur du plan de coupe de V2 à l'image, retouche manuelle toujours `manuel`.

## 4. Limites du rendu (affichées, jamais silencieuses)
Transitions et fondus vidéo non rendus (avertissement) ; vitesse ≠ 1 refusée ; étalonnage/LUT non rendus ; proxy = demi-résolution recalculée depuis les originaux.

## 5. Calibration sur vrais rushes Sony A7 IV 4K50 / multicam (plan)
Seuils provisoires (−35 dB, plans 2–6 s) = paramètres de `StyleParams`, pas des constantes. Protocole : une séquence de référence du réalisateur, 3 réglages comparés à son montage manuel (durée, nombre de plans, % de plans conservés tels quels), mesures reportées dans `docs/CALIBRATION_MONTAGE.md`. Lecture seule des rushes ; aucune installation intermédiaire.

## 6. Performances / GPU
À mesurer sur la station Windows lors d'**une** session d'essai groupée (lecteur 4K50, export, NVENC) — pas par installateurs intermédiaires.

## 7. Formats mixtes : 4K50 et 1080p50, export 4K ou 1080p
- **Format du projet** : à l'import, un projet vide prend la cadence dominante des rushes (50) et la plus grande résolution (4K si présente) — `ProjectController.detect_format/apply_format` (op `SetProject`, annulable). La cadence ne peut plus changer une fois des plans posés (les coupes seraient déplacées).
- **Rushes de tailles différentes** dans la même timeline : chacun est mis à l'échelle (bandes si le ratio diffère) vers la résolution de sortie.
- **Export au choix** : `4k` (3840×2160), `1080p` (1920×1080) ou taille libre ; dialogue « Format d'export » dans la fenêtre ; 50 i/s conservées ; le cache de segments tient compte de la résolution (changer de format ré-encode, un même format réutilise).
- Limite : un export 4K depuis des sources 1080p est un agrandissement (aucun détail créé). Débit réel 4K50 / NVENC à mesurer sur la station (7,5 s pour 2 s de 4K50 en encodage logiciel dans le bac à sable, non représentatif).
