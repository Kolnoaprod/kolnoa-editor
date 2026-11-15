# Kolnoa Editor

Montage de films de mariage assisté par IA (Claude et ChatGPT), pensé pour tourner sur le Shadow PC (Windows + GPU).

## Principe

1. **L'IA décide, ffmpeg exécute.** Le montage est décrit dans une *liste de décisions* (JSON) : quels plans, quelles durées, quelle musique. Kolnoa la rend en MP4 localement.
2. **Les rushes ne bougent pas.** Kolnoa lit directement le dossier du mariage (Google Drive pour ordinateur, ex. `G:\Mon Drive\KOLNOA\MARIAGE …`), crée des proxys légers et n'envoie à l'IA que du texte (transcriptions, descriptions de plans, listes de décisions).
3. **Les retours sont précis.** Un commentaire posé sur une image du rendu est rattaché au segment source (id stable) : il reste valable après la version suivante.

## Installation (Windows)

```powershell
winget install Gyan.FFmpeg
git clone https://github.com/Kolnoaprod/kolnoa-editor
cd kolnoa-editor
py -m pip install -e ".[dev]"
py -m pytest
```

NVENC (GPU) est détecté automatiquement ; `KOLNOA_NO_NVENC=1` force le rendu CPU.

## Utilisation

```powershell
$M = "G:\Mon Drive\KOLNOA\MARIAGE ELIE ET REBECCA FELLOUS"

kolnoa scan $M                       # inventaire par dossier et appareil (A7IV, DRONE, OP4, CANON, audio)
kolnoa scan $M --probe               # + durée et présence d'audio de chaque fichier
kolnoa proxies $M --subdir MAIRIE    # proxys 540p dans <mariage>\.kolnoa\proxies
kolnoa validate $M v001.json         # vérifie une liste de décisions
kolnoa render $M v001.json           # rendu rapide depuis les proxys
kolnoa render $M v001.json --final   # rendu final depuis les originaux
kolnoa comment $M v001.json 133.4 "couper plus tôt"
kolnoa feedback $M v001.json         # texte des retours à coller à l'IA
```

Tout ce que Kolnoa génère est dans `<mariage>\.kolnoa\` (jamais dans tes rushes).

## État

| Brique | État |
| --- | --- |
| Scan du dossier, détection appareils et dates | fait |
| Proxys 540p (NVENC ou x264) | fait |
| Liste de décisions (schéma, validation) | fait |
| Rendu proxy / final, musique, fondus, drone sans son | fait |
| Retours rattachés aux segments + texte pour l'IA | fait |
| Transcription (faster-whisper GPU) | à faire |
| Synchro multi-caméras par l'audio, décalage d'horloge drone | à faire |
| Analyse des plans (IA vision) et premier montage proposé | à faire |
| Appels API Claude / OpenAI | à faire |
| Lecteur de revue image par image (interface) | à faire |

Voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) et [docs/STYLE_GUIDE.md](docs/STYLE_GUIDE.md).
