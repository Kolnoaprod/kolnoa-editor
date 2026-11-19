# Kolnoa Editor V0.1 — lisez-moi et bilan

> **État honnête** : tout ce qui est marqué « fonctionnel » l'est *sous Linux, sur rushes synthétiques*. La CI Windows (workflow `windows-editor-v01`)
> rejoue les tests et l'autotest de l'EXE sous Windows ; **la validation sur votre PC et vos vrais rushes reste à faire**.

## Lancer
1. Télécharger `KolnoaEditor_V0.1.zip` (page du run GitHub Actions « Kolnoa Editor V0.1 », section *Artifacts*), dézipper.
2. Double-clic sur `KolnoaEditor.exe` (non signé : SmartScreen → « Informations complémentaires » → « Exécuter quand même »).
3. Choisir le dossier qui contient les rushes (il n'est **jamais modifié** ; le projet vit dans `Documents\Kolnoa_Projets\<dossier>-<id>`).
4. **Parcours de test sans vos fichiers** : en PowerShell, `KolnoaEditor.exe --demo C:\Temp\demo` crée 3 rushes synthétiques et les ouvre.
   `KolnoaEditor.exe --selftest` rejoue tout le parcours sans fenêtre (code de sortie 0 = OK).

## Utiliser
- **Monte-moi cette séquence** (bouton ou champ de texte) → premier montage sur la timeline.
- **Espace** lecture/pause · clic sur la règle = tête de lecture · aperçu image sous la tête.
- **S** rasoir · tirer un bord = rogner (Alt = ripple) · glisser un plan = déplacer · **Ctrl+Z / Ctrl+Maj+Z** annuler/rétablir.
- Sélectionner un plan (ou placer la tête / poser **I** et **O**) puis écrire : « raccourcis ce plan », « accélère le rythme », « ralentis à 50 % »…
- **Ctrl+S** enregistre (instantané) · **Ctrl+E** exporte en MP4 (4K ou 1080p au choix).

## Les 9 points de la V0.1
| # | Besoin | État | Détail |
|---|---|---|---|
| 1 | Application Windows | **Partiel** | EXE fabriqué et autotesté par la CI ; non validé sur votre machine. |
| 2 | Ouvrir un projet, importer Sony / Osmo / drone | **Partiel** | Import de dossier (mp4, mov, mxf, mts, m4v), cadence/taille détectées. Aucun essai sur de vrais fichiers Osmo/drone. |
| 3 | Visualiser rushes et timeline | **Fonctionnel** | Liste par séquence, timeline avec formes d'onde. |
| 4 | Lire / pause / tête de lecture | **Partiel** | Lecture = tête qui avance + **image** (pas de son en lecture). Fluidité 4K50 non mesurée ; lecteur multicam séparé (GPU) conservé. |
| 5 | Couper, raccourcir, déplacer, annuler | **Fonctionnel** | Rasoir, rognage, déplacement, annulation illimitée, protections du travail manuel. |
| 6 | Montage automatique | **Fonctionnel (synthétique)** | Qualité sur vos vrais rushes non évaluée. |
| 7 | Consigne textuelle sur plan / tête de lecture | **Fonctionnel** | Directives : raccourcir, rythme, plan de coupe, ralenti/accéléré (#41, #42). |
| 8 | Enregistrer / rouvrir | **Fonctionnel** | Journal + instantanés ; test de réouverture. |
| 9 | Export MP4 1080p / 4K | **Fonctionnel (Linux)** | Image + son ; durée vérifiée. Non vérifié sous Windows/NVENC ; fondus et transitions vidéo non rendus (avertissement affiché). |

## Problèmes connus
- Pas de son pendant la lecture (le son se juge à l'export).
- Fondus/transitions vidéo, LUT : non rendus.
- Stabilisation, extension générative : non livrées (prévues après la V0.1).
- Regroupement multicam manuel (caméras sans horodatage fiable) : non fait ; ducking audio : non fait.
- Seuils d'analyse (choix d'angle, rythme) jamais calibrés sur de vrais rushes.
