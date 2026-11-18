# Multicam automatique, lecteur dans la fenêtre, musique

Branche `feat/multicam-auto`, empilée sur `feat/beta-integration` (#37). Brouillon.

## Chaîne
1. **Regroupement** (`auto/multicam.propose_groups`) : dans une même séquence (dossier), les rushes dont les plages horaires se chevauchent d'au moins 5 s forment un groupe ; le maître = le rush avec son le plus long. L'horodatage ne sert que d'**indice** (il peut être faux).
2. **Synchro** (moteur audio existant) : indice = décalage d'horodatage ± 90 s ; l'angle fiable devient `ok`, sinon `impossible`/`incertain` avec la raison. Le maître est `reference`.
3. **Choix d'angle** (`choose_angles`) : plans de `shot_min`–`shot_max` s sur les moments sonores du maître ; angle **utilisable et couvrant tout le plan** ; jamais deux fois de suite le même ; sinon le moins utilisé ; déterministe. Un angle non fiable n'est **jamais** choisi et est signalé dans le compte rendu.
4. **Génération** : plans multicam (son = maître) sur V1, rushes sans groupe montés à plat, le tout dans l'ordre chronologique, **un seul geste d'annulation** (les groupes imbriqués rejoignent le groupe en cours).
5. **Fenêtre** : le lecteur multicam apparaît dès qu'un groupe existe (la tête de lecture le pilote) ; sur un plan sélectionné, `switch_angle` propose seulement les angles utilisables (même instant, même son maître, annulable, protégé comme retouche manuelle).
6. **Musique** (`add_music`) : plan audio ordinaire sur `A2` (fichier lu en place), niveau −14 dB par défaut, fondu de sortie, coupé à la fin du film ; mixé par le même moteur, modifiable à la main, annulable.

## Preuves (vrais fichiers)
Deux caméras, même son, départs décalés de 3 s et **horodatage faux de 2 s** → décalage retrouvé à ±0,05 s ; les deux angles sont utilisés ; export MP4 avec son ; un `Ctrl+Z` retire montage ET groupe. Deux caméras de sons différents → angle écarté et signalé. Musique présente dans le MP4, retirée par annulation.

## Limites (dites)
- Le choix d'angle n'a **aucune qualité visuelle** (netteté, cadrage, stabilité, visages) : rotation équitable seulement. C'est la prochaine mesure à ajouter, sur tes rushes.
- Pas de baisse automatique de la musique sous la parole (ducking) ; niveau à régler à l'oreille.
- Le lecteur réel (`MulticamPlayer` + Qt Multimedia) n'est testé ici que par un double ; sa fluidité 4K50 est à mesurer sur la station.
- Synchro testée sur sons simulés ; à valider sur de vrais événements (salle, enregistreur).
