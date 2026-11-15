# Architecture

## Boucle de travail

```
dossier mariage (Drive) ──scan──▶ library.json ──proxys──▶ .kolnoa/proxies
                                        │
        transcription · synchro · analyse des plans (à venir)
                                        │
                                        ▼
   guide de style + analyses ──▶ IA (Claude / ChatGPT) ──▶ liste de décisions vNNN.json
                                                                   │
                                                          render (proxy / final)
                                                                   │
                                                                   ▼
                                       film MP4 ──revue──▶ retours (rattachés aux segments)
                                                                   │
                                                  prompt de retours ──▶ IA ──▶ v(NNN+1)
```

## Données Kolnoa (`<mariage>\.kolnoa\` par défaut, ou `KOLNOA_DATA_DIR`)

| Chemin | Contenu |
| --- | --- |
| `library.json` | inventaire des rushes : chemin relatif, appareil, groupe, date de prise, durée, audio |
| `proxies/…` | proxys 540p, même arborescence que les rushes ; chaque proxy a une fiche `.proxy.mp4.json` (empreinte de la source, hauteur) |
| `cache/segments/` | segments déjà encodés (clé = source, plage, fondus, gain, cadence, résolution, encodeur) : reprise et rendus incrémentaux |
| `tmp/`, `logs/kolnoa.log` | fichiers de travail, journal tournant |
| `versions/vNNN.json` | listes de décisions |
| `comments/vNNN.json` | retours de revue |
| `renders/vNNN_proxy.mp4`, `vNNN_final.mp4` | rendus |

## Liste de décisions (schéma 1)

```json
{
  "schema": 1,
  "title": "La Mairie",
  "version": 3,
  "fps": 25,
  "resolution": [1920, 1080],
  "source_gain_db": -8,
  "music": { "path": "MUSIQUE/titre.mp3", "start": 0, "gain_db": 0, "fade_out": 2 },
  "segments": [
    {
      "id": "s001",
      "clip": "MAIRIE/A7IV/20261011_C4837.MP4",
      "start": 1.2,
      "end": 5.0,
      "audio": "source",
      "fade_in": 0.3,
      "fade_out": 0,
      "note": "arrivée devant l'Hôtel de ville"
    }
  ]
}
```

Règles :
- `clip` est relatif au dossier du mariage, avec des `/`. Un chemin qui sort du dossier est refusé.
- `start` / `end` en secondes dans le clip source ; `end > start`.
- `audio` : `source` (son du clip) ou `mute`. Un clip sans piste son (drone) reçoit une piste silencieuse.
- **Les ids de segments sont stables** : l'IA ne les change pas, elle en ajoute de nouveaux pour les nouveaux plans. C'est ce qui garde les retours valides d'une version à l'autre.

## Retours

`kolnoa import-version` enregistre la liste de l'IA comme version suivante et **reporte les retours ouverts** : ils suivent leur plan s'il est déplacé, raccourci ou scindé (`parent`), et deviennent `orphan` s'il est supprimé (les ids supprimés sont listés dans `retired` et jamais réutilisés). `kolnoa diff` montre l'avant/après.

`kolnoa comment` (et plus tard le lecteur de revue) transforme « 133,4 s du rendu v3 » en `segment s047 · clip … · 12,6 s dans le clip`. `kolnoa feedback` produit le texte à donner à l'IA avec la consigne de ne modifier que les segments concernés.

## Synchro et horloges

- Chaque appareil a sa propre horloge. Les noms DJI et Canon contiennent l'heure de prise ; le Sony n'a que la date (l'heure vient de la date de modification, approximative).
- Le drone n'a pas de son : on le place avec son horodatage, après calibrage d'un décalage par appareil et par mariage sur un repère visible dans plusieurs caméras ; ajustement fin au clavier dans la revue.
- Les enregistreurs audio (`4CH….wav`) n'ont pas d'heure fiable : synchro par corrélation avec le son du A7 IV.

## Choix techniques

- Python 3.10+, bibliothèque standard + ffmpeg/ffprobe (aucune dépendance pip pour le cœur).
- Encodage NVENC si disponible (Shadow PC), sinon x264.
- Rendu par segments (H.264 à nombre d'images exact + audio PCM) mis en cache, concaténés sans ré-encodage vidéo ; l'audio (son direct + musique) est encodé en AAC une seule fois. Écriture en `.part` puis renommage ; durée vérifiée.
- Bibliothèque incrémentale : empreinte = taille + date de modification (+ hash rapide optionnel) ; jamais d'écrasement des fiches existantes.
- Chemins Windows pris en charge (`pathlib`, séparateurs normalisés en `/` dans les données).
