# Calibration de la synchro sur de vrais rushes (`kolnoa sync-calibrate`)

Outil : `src/kolnoa_editor/sync/calibrate.py`, commande `kolnoa sync-calibrate`. Il mesure le moteur de synchro (PR #9)
sur des rushes réels et produit un rapport. **Il ne modifie jamais les originaux** et rien n'est publié dans le dépôt :
ce dépôt est public, les rapports (noms de fichiers, durées) restent dans le dossier `--out` du réalisateur.

## Garanties (testées dans `tests/test_sync_calibrate.py`)

- Lecture seule : seuls `ffprobe` et `ffmpeg -i … -f rawvideo/f32le` lisent les médias ; aucune écriture, création,
  renommage ni suppression dans le dossier de rushes. Le dossier de rapport ne peut pas être dans les rushes (refusé).
- Taille et date de chaque fichier lu sont relevées avant et après ; le rapport affiche « Originaux inchangés : oui/NON »
  et la commande sort en code 3 si un original a changé.
- `--dry-run` : inventaire ffprobe et plan seulement, aucun média décodé. Rien n'est copié, rien n'est téléversé.
- Chemins Windows (`G:\Mon Drive\KOLNOA\MARIAGE …`) acceptés tels quels sous Windows ; ailleurs, `--root-map` indique où
  le dossier est monté.

## Utilisation (Windows, depuis un clone du dépôt, branche `feat/multicam-sync`)

Prérequis : Python 3.10+, `pip install numpy`, `ffmpeg` et `ffprobe` dans le PATH.

```bat
set PYTHONPATH=src
:: 1. Plan sans rien décoder
python -m kolnoa_editor.cli sync-calibrate "G:\Mon Drive\KOLNOA\MARIAGE YOHANNA ET ARTHUR 07-10092026" --scenario mairie --out "C:\Kolnoa\calibration\mairie" --dry-run
:: 2. Analyse réelle (MIC 1 / MIC 2 / CAM 1 / CAM 2)
python -m kolnoa_editor.cli sync-calibrate "G:\Mon Drive\KOLNOA\MARIAGE YOHANNA ET ARTHUR 07-10092026" --scenario mairie --out "C:\Kolnoa\calibration\mairie" --with-hint
:: 3. JEREMY : caméra, audio 4 pistes, drone
python -m kolnoa_editor.cli sync-calibrate "G:\Mon Drive\KOLNOA\MARIAGE YOHANNA ET ARTHUR 07-10092026" --scenario jeremy --out "C:\Kolnoa\calibration\jeremy" --with-hint --max-video-seconds 600
```

Options utiles : `--only TEXTE` (ne garder que les fichiers dont le chemin contient TEXTE), `--max-pairs`, `--clock-tol`
(écart d'horloge toléré pour choisir les paires, 7200 s par défaut), `--inventory-only` (inventaire + test de silence audio),
`--config sync.json`, `--tz Europe/Paris`.

Sorties dans `--out` : `report.md` (lisible), `report.json`, `results.csv`, `inventory.csv`, `plan.txt`,
`suggested_sync_config.json` (proposition, jamais appliquée), `cache/` (séries de luminosité, jetables).

### Repères manuels (la seule vraie mesure d'erreur)

Sans repère vérifié à l'image, le rapport ne peut dire que la cohérence interne. Pour mesurer une erreur absolue, noter un
même instant réel (un claquement, un flash) dans deux fichiers et le déclarer :

```json
[{"ref": "RUSHES/MAIRIE/AUDIO/MIC 1/DJI_01_20260903_102305.WAV",
  "sig": "RUSHES/MAIRIE/AUDIO/MIC 2/DJI_01_20260903_102303.WAV",
  "t_ref": 612.480, "t_sig": 614.330}]
```
`--truth reperes.json` ajoute l'erreur en millisecondes de chaque paire concernée. Le rapport donne aussi la **fermeture
en boucle** (A→M, B→M, A→B doivent s'accorder) et l'**écart avec les métadonnées**.

## Ce que les vrais rushes ont déjà montré (extraits de MAIRIE, 15/11/2026)

Seuls les fichiers de moins de 400 Mo ont pu être copiés dans l'espace de calcul d'où ce rapport a été produit ; les gros
fichiers (voir « Non mesuré ») doivent être analysés par la commande ci-dessus, sur votre machine.

| Constat réel | Conséquence dans le code |
|---|---|
| **Toutes les caméras Sony examinées (8 clips, MAIRIE et JEREMY) ont une piste audio à zéro numérique** (−91 dB, PCM 48 kHz stéréo présent mais vide). Le DJI de CAM 2 n'a pas de piste audio du tout. | Aucune synchro audio caméra↔micro n'est possible sur ces clips. Le moteur le dit (« piste audio vide ») au lieu de « aucune correspondance » ; l'outil ne propose plus ces clips en audio et les range en visuel/manuel. |
| MIC 1 / MIC 2 (DJI Mic, 48 kHz flottant mono, 30,8 min, longueurs identiques) : décalage ≈ −1,85 s, enveloppes très nettes (PSR 75) mais, sur 12 fenêtres de 20 s à ±60 s, **4 fenêtres sur 9 « valides » sont de faux pics isolés** (PSR 8,8–16,9, décalages de −44 s à −4 s) et 3 autres sont rejetées (PSR 6,4–7,5, décalages de +54 s à +57 s). | La détection de discontinuités par sauts consécutifs fragmentait le clip en 7 « segments ». Remplacée par un **consensus** (droite décalage/dérive ayant le plus d'inliers) : les pics isolés sont ignorés avec une note ; une discontinuité demande ≥ 2 fenêtres contiguës cohérentes. Le suivi « dernière fenêtre » qui propageait un faux pic est supprimé. Le PSR seul ne distingue pas un faux pic d'un vrai (faux jusqu'à 16,9, et 20,5 avant la suppression du suivi ; vrais de 8,8 à 31,6) : c'est l'accord entre fenêtres qui décide. |
| Les mêmes micros : décalage −1,856 s (t = 720–960 s) puis −1,834 s (t = 1200–1560 s), soit un **pas de 22 ms** et ±25 ms de gigue entre fenêtres faibles. | `inlier_tol_s` 0,03 → **0,05 s**. Avec le premier moteur, ce pas se traduisait par une dérive de −65 à −67 ppm ; avec 0,05 s et le consensus : −9,5 ppm. La dérive reste non fiable sur ce genre de signal. Résultat final de la paire : `incertain`, décalage −1,850 s, 5 fenêtres cohérentes sur 9 valides. |
| Créations de fichiers : balises `creation_time` des Sony en avance d'**1 h** sur la date de modification du même fichier ; noms DJI en heure locale ; balises DJI cohérentes. | `clock_tolerance_s` de 300 s est trop strict pour choisir des paires : l'outil utilise 7200 s (`--clock-tol`) ; les métadonnées restent un indice, jamais une synchro. À terme : décalage d'horloge appris par appareil après une première synchro sûre. |
| Luminosité moyenne de 7 vidéos réelles (intérieur de mairie) : variation image à image **≤ 2,3 niveaux** (médiane ≈ 0,1) ; le seuil d'événement est 8. | **Aucun événement lumineux détecté** (0/0) : la synchro visuelle par événements de luminosité globale ne trouve rien sur ce type de scène. Elle reste expérimentale et non démontrée ; les signaux synthétiques (flashs de +35 à +60 niveaux) étaient trop optimistes. Pistes : énergie de mouvement, flashs d'appareils photo, repères de luminosité locale — non implémentées, à décider avec ChatGPT. L'alignement manuel côte à côte reste le chemin fiable. |
| Piste audio d'un enregistreur 30 min : calcul de la paire MIC 2 → MIC 1 en ≈ 15 s ; décodage logiciel de 120 s de HEVC 4K ≈ 4 min sur le serveur de calcul. | `--max-video-seconds` borne la lecture vidéo (300 s par défaut). |

## Non mesuré (et pourquoi)

- `JEREMY/AUDIO` (4CH000I/M.wav 0,94 Go, 4CH001I/M.wav 1,84 Go) et `JEREMY/DRONE` (14 fichiers de 1,4 à 3,8 Go) dépassent
  la limite de 400 Mo de la copie vers l'espace de calcul : **la synchro caméra / audio / drone de JEREMY n'a pas été mesurée**.
  Seul l'inventaire de 2 clips caméra JEREMY (audio vide) a été possible. À lancer sur votre machine avec la commande 3.
- `CAM 1` C4250 (5,6 Go), `CAM 2` 0006 (12,9 Go), JEREMY `C4558` (29 Go) : idem (prise longue : le candidat naturel pour la dérive).
- Aucun repère manuel n'a encore été posé : **l'erreur absolue de chaque décalage n'est pas démontrée**, seulement la cohérence
  interne.
- Les micros portés par deux personnes ne captent pas exactement les mêmes sons : le résultat MIC 2 → MIC 1 est une mesure
  difficile ; la vraie cible (caméra avec son ↔ enregistreur) n'a pas pu être testée puisque les caméras sont muettes.

## Réglages par défaut après calibration (valeurs expérimentales, pas de production)

| Réglage | Avant | Après | Source |
|---|---|---|---|
| `inlier_tol_s` | 0,03 | 0,05 | gigue ±25 ms entre fenêtres de 2 micros réels |
| `max_unmatched_ratio` | — | 0,25 | clip en partie hors enregistrement : jamais « ok » |
| `coarse_ambiguity_ratio` / `_exclusion_s` | — | 0,85 / 3 s | même musique jouée deux fois (revue PR #9) |
| `empty_audio_peak` | — | 1e-5 | pistes Sony à −91 dB |
| `visual_event_min_gap_s` | — | 1 s | un flash = un événement (revue PR #9) |
| `min_psr`, `ok_confidence`, `uncertain_confidence`, `visual_*` | inchangés | inchangés | **pas assez de données réelles** pour les déplacer honnêtement |


## Progression et annulation
La commande affiche l'avancement (`[inventaire]`, `[audio]`, `[visuel]`, `[rapport]`) et s'arrête proprement avec Ctrl+C : processus ffmpeg arrêté, aucun rapport partiel, rushes intacts, code de sortie 130. Voir [TASKS.md](TASKS.md).
