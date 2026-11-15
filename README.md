# Kolnoa Editor

Montage de films de mariage assisté par IA (Claude et ChatGPT), pensé pour tourner sur le Shadow PC (Windows + GPU). Objectif : un logiciel de montage principal, avec un premier montage automatique puis des finitions à la souris ou en langage naturel.

Feuille de route : issues [#1](https://github.com/Kolnoaprod/kolnoa-editor/issues/1) et [#2](https://github.com/Kolnoaprod/kolnoa-editor/issues/2). Architecture : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Audit du socle : [docs/AUDIT.md](docs/AUDIT.md).

## Principe

1. **Les rushes originaux ne sont jamais modifiés.** Kolnoa les lit en place (ex. `G:\Mon Drive\KOLNOA\MARIAGE …`) et écrit ses données ailleurs.
2. **L'IA propose, tu décides.** Le montage est une liste de décisions (JSON) rendue par ffmpeg. Aucun média n'est envoyé à une API sans ton accord explicite.
3. **Les retours suivent les plans.** Un commentaire est rattaché au plan source (id stable) et suit ce plan à travers les versions.

## Installation (Windows)

```powershell
winget install Gyan.FFmpeg
git clone https://github.com/Kolnoaprod/kolnoa-editor
cd kolnoa-editor
py -m pip install -e ".[dev]"
py -m pytest
```

NVENC (GPU) est détecté automatiquement ; `KOLNOA_NO_NVENC=1` force le rendu CPU.

**Données hors du Drive** (recommandé : proxys, cache et rendus peuvent peser des dizaines de Go) :

```powershell
setx KOLNOA_DATA_DIR "D:\Kolnoa"      # puis rouvrir le terminal ; ou --data-dir à chaque commande
```

Sans cela, les données vont dans `<mariage>\.kolnoa\`, donc dans le Drive.

## Utilisation

```powershell
$M = "G:\Mon Drive\KOLNOA\MARIAGE ELIE ET REBECCA FELLOUS"

kolnoa scan $M                       # inventaire incrémental (ne perd jamais les fiches déjà lues)
kolnoa refresh $M --probe --hash     # + durée/audio des nouveaux fichiers + empreinte de contenu
kolnoa proxies $M --subdir MAIRIE    # proxys 540p ; relance = reprise, rien n'est refait inutilement
kolnoa check $M v001.json --final    # vérifications avant rendu (sources, plages, VFR, disque…)
kolnoa render $M v001.json           # rendu rapide depuis les proxys (Ctrl+C = reprise possible)
kolnoa render $M v001.json --final   # rendu final depuis les originaux
kolnoa import-version $M nouvelle.json   # enregistre la liste de l'IA comme version suivante, reporte les retours
kolnoa diff $M 1 2                   # avant/après entre deux versions
kolnoa comment $M v001.json 133.4 "couper plus tôt"
kolnoa feedback $M v001.json         # texte des retours ouverts à donner à l'IA
kolnoa clean-cache $M                # vide le cache de segments (jamais les rushes ni les rendus)
```

Journal détaillé : `<données>\logs\kolnoa.log`.

## État

| Brique | État |
| --- | --- |
| Scan incrémental, empreintes, fichiers manquants | fait (PR 1) |
| Proxys invalidables et reprenables | fait (PR 1) |
| Validation avant rendu (`check`) | fait (PR 1) |
| Rendu atomique, annulable, avec cache et reprise ; raccords exacts à l'image | fait (PR 1) |
| Filiation des ids de segments, versions, retours reportés, diff | fait (PR 1) |
| Timeline native, opérations réversibles | à faire (PR A) |
| Multicam, synchro audio / visuelle / manuelle | à faire (PR B) |
| Interface timeline, lecteur proxy | à faire (PR C) |
| Effets, LUTs, transitions, musique, mixage | à faire (PR D) |
| Assistant en langage naturel, montage automatique, « Rythmer » | à faire (PR E) |
