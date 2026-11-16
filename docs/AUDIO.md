# Moteur audio (PR dédiée, base : #14)

Décisions techniques prises pour répondre à la validation de ChatGPT sur la PR #14. Rien n'est fusionné. Tout ce qui suit
est testé avec de **vrais rendus ffmpeg de sources synthétiques mesurées** (pas de valeur supposée) ; **aucune mesure sur de
vrais rushes n'a encore été faite**.

## Principe : trois sujets séparés
| Sujet | Où ça vit | Qui décide |
|---|---|---|
| Synchronisation (offset, dérive, état) | `Angle` (PR #9) | l'algorithme, validé par l'humain |
| Choix de la source qu'on entend | `MasterRange` (`assign_master`) | **l'humain** |
| Qualité sonore mesurée | `audio_quality` (`propose_master`) | personne : elle conseille |

Une source non synchronisée de façon fiable est **écartée** de toute proposition et **refusée** par `assign_master`. Une
proposition ne modifie jamais la timeline (testé) ; pour l'appliquer : `assign_master(..., reason=proposition.ranked[0].reason())`.

## Modèle (schéma 1 inchangé : champs optionnels, anciens fichiers relus sans migration)
- `Track.solo`, `Clip.strips` (canaux de sortie), `Clip.fade_in_curve` / `fade_out_curve`.
- `Timeline.masters` : source audio **par séquence**, en secondes de l'**axe du groupe** (donc stable quand on coupe, déplace, ripple ou change d'angle : le décalage est conservé par construction, les plages ne sont pas liées aux plans).
- `Timeline.crossfades` : fondu enchaîné entre deux plans voisins, centré sur la coupure, ½ durée de chaque côté (demande de la matière source ; le modèle vérifie l'entrée du plan de droite, `source_len` optionnel vérifie la sortie du plan de gauche).
- `Timeline.audio_settings` : fréquence de mixage (48 kHz par défaut), plafond de crête (−1 dBTP), cible de sonie (aucune par défaut), fondu de changement de source (10 ms).
- Opérations réversibles et journalisées : `SetClipStrips`, `AddMaster`/`RemoveMaster`, `AddCrossfade`/`RemoveCrossfade`, `SetAudioSettings` (+ `SetClipProps` et `SetTrackProps` étendus).

## Édition (`timeline/audio_edit.py`, une Transaction chacune)
`set_clip_gain`, `set_track_audio` (gain, muet, **solo**), `set_fades` (durée + courbe `linear`/`equal_power`/`log`), `to_mono`, `to_dual_mono`, `to_stereo`, `set_strip`, `reset_channels`, `add_crossfade`/`remove_crossfade`, `assign_master`/`clear_master`/`master_at`, `set_audio_settings`.

**Dual mono** = deux canaux de sortie **indépendants** (gain, muet, solo propres), pas une simple duplication : un fichier stéréo avec un micro par canal devient deux monos réglables séparément ; un mono peut être réparti en deux canaux réglables séparément. Le routage d'origine (`strips=None`) reste : mono → les deux côtés à gain unitaire, stéréo → telle quelle. Les canaux explicites utilisent une loi de pan −3 dB (centre = −3 dB de chaque côté, côté dur = 0 dB).

**Garde-fou** : un fondu enchaîné lie deux plans ; couper, déplacer ou ripple qui le casserait est **refusé** avec un message (supprimer le fondu d'abord), jamais une désynchronisation silencieuse.

## Mixage (`timeline/audio_mix.py`)
`build_mix_plan` (pur) → événements ; `render_mix(tl, dossier_médias, sortie.wav)` → WAV 24 bits stéréo + `MixReport` mesuré.
- Gains cumulés : global + piste + plan + canal. Muet / solo : piste et canal. Plan « muet » ou source sans piste audio : ignoré, avec avertissement.
- Plans multicam `audio=group` : le son suit l'axe du groupe ; la source change aux limites des `MasterRange` avec un fondu de 10 ms (réglable) ; si une source choisie n'est plus fiable, **retour au maître avec avertissement**.
- **Dérive** : corrigée par rééchantillonnage (une dérive est une erreur d'horloge d'échantillonnage) ; `atempo` rejeté après mesure (ses fenêtres déplaçaient les transitoires de plus de 20 ms). Résidu d'arrondi ≈ 2 ppm ; la hauteur bouge de la dérive (quelques cents, inaudible). Testé : 2000 ppm sur 62 s, clics recalés à ±3 ms, et la même timeline sans correction se décale de plus de 50 ms.
- **44,1 / 48 kHz** : chaque source est rééchantillonnée à la fréquence du projet (testé : fréquence et durée conservées).
- **Headroom / sonie** : (1) mixage en flottant, jamais écrêté ; (2) mesure EBU R128 (sonie intégrée, crête vraie) ; (3) option `target_lufs` : un gain constant (pas de compression) ; (4) si la crête dépasse le plafond, limiteur ; la latence de 5 ms de l'anticipation du limiteur est **compensée** (défaut trouvé par les tests : tout le mixage glissait de 5 ms). Le limiteur travaille sur la crête d'échantillon ; la crête vraie du fichier écrit est **mesurée et rapportée**, avec avertissement si elle dépasse le plafond.
- Sources jamais modifiées (empreinte vérifiée par test).

## Qualité sonore et proposition (`timeline/audio_quality.py`)
Mesures par source et par séquence : sonie, crête vraie, saturation (part d'échantillons à pleine échelle), plancher de bruit (10ᵉ centile des niveaux sur 50 ms), contraste (centiles 90 − 10, **pas** un rapport signal/bruit), silence (sous −80 dBFS = micro coupé ; une pause de parole n'est pas du silence), corrélation des canaux (phase). Chaque critère a une valeur, une note 0–1, un poids et une phrase d'explication ; le classement se contrôle ligne par ligne (`proposition.explain()`). Si l'écart entre les deux premiers est < 0,08 : « à départager à l'oreille ». Les limites sont écrites dans chaque proposition (intelligibilité, naturel de la prise de son, intérêt éditorial **non mesurés**).

**Seuils et poids = points de départ**, à recaler avec `kolnoa audio-measure` sur de vrais rushes (« mesures sur vrais rushes ensuite »).

## Ligne de commande
`audio-measure FICHIER…` (lecture seule) · `audio-propose RACINE GROUPE DÉBUT FIN` (n'applique rien) · `audio-master RACINE GROUPE DÉBUT FIN SOURCE [--reason] [--clear]` · `audio-mix RACINE SORTIE.wav [--lufs] [--ceiling] [--force]`.

## Compatibilité avec #9 / #11 / #12 / #13 / #14
Mêmes ops, même journal, même session (annulation/rétablissement/réouverture testés). Branche **empilée sur #14** (donc sur #13 et #9) : le diff contient aussi ces PR tant qu'elles ne sont pas fusionnées. Fichiers partagés susceptibles de conflit avec #11/#12 : `timeline/model.py`, `timeline/ops.py`, `timeline/__init__.py`, `cli.py`, `convert.py` (ajouts en fin de zone, rebase mécanique). L'export vers la liste de décisions **avertit** de tout réglage audio qu'il ne sait pas porter (canaux, fondus enchaînés, plages de source, solo, courbes) : le rendu audio complet passe par `render_mix`.

## Pas fait / à mesurer
- Rendu vidéo + audio intégré au MP4 final (phase D) : `render_mix` produit le WAV, le muxage reste à faire. Musique (`Timeline.music`) non incluse dans `render_mix`.
- Interface (mixeur, formes d'onde) ; automation de volume par points (enveloppes) ; EQ/compression ; détection de parole.
- **Aucune mesure sur de vrais rushes** (seuils de qualité, comportement du limiteur sur de la voix, dérives réelles) ; vérifier aussi qu'une version de ffmpeg récente est installée (`alimiter`, `ebur128`, `adelay …S`).
- Un fondu enchaîné ne survit pas à une coupe/déplacement des plans qu'il lie (refus explicite) ; une reconduite automatique est possible plus tard.
