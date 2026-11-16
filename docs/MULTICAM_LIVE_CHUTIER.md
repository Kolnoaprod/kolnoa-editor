# Multicam à la main et chutier (C3a/C3b, moteur)

Même principe que `EDITING.md` : chaque fonction renvoie **une Transaction** (un pas d'annulation, tout ou rien), utilisée à
l'identique par l'interface, la CLI et les prompts. Aucun média n'est lu ni modifié.

## Touches 1 à 9 (`timeline/multicam_live.py`)
| Fonction | Effet |
|---|---|
| `key_angles`, `angle_for_key` | touche N = Nᵉ angle **image** du groupe ; le micro (audio seul) n'a pas de touche ; au plus 9 |
| `cut_to_angle(tl, piste, image, angle)` | une touche : coupe le plan multicam à l'image et montre l'angle à partir de là (au début d'un plan : pas de coupe) |
| `record_angle_switches(tl, piste, [(image, angle), …])` | une prise entière en **un seul pas d'annulation** ; touches dans le désordre acceptées ; touche sans effet ignorée |

Garanties : même instant de groupe avant et après la touche (l'image change, le son maître ne bouge pas) ; un angle non
synchronisé de façon fiable refuse toute la prise ; piste verrouillée refusée ; rejeu du journal et réouverture de session testés.

## Chutier (`timeline/chutier.py`, `Timeline.bin`, schéma 1 inchangé : champ optionnel)
| Fonction | Effet |
|---|---|
| `mark_subclip` | I puis O : plage [entrée, sortie] d'un rush (refuse entrée ≥ sortie, sortie au-delà de la durée connue) |
| `adjust_subclip`, `remove_subclip`, `search_bin` | re-régler, supprimer, chercher par nom/note/étiquette/média |
| `place_subclip(tl, id, piste, image, mode, audio_track)` | glisser sur une piste : recouvrement (défaut, rien d'autre ne bouge) ou insertion ; avec `audio_track`, le son est posé en même temps et lié |

## Pas encore fait
Interface (touches réelles, lecture, glisser-déposer) ; waveforms ; plusieurs sources audio par séquence et choix de la meilleure
piste ; mixage (mono → dual mono, niveaux, fondus) ; durées de rushes lues automatiquement (`source_len` reste fourni par l'appelant).
