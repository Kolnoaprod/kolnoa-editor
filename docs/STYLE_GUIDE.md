# Kolnoa Editor — Guide de style de montage (v1, à valider sur rushes)

> **Statut** : préférences explicites du réalisateur + propositions de règles opérationnelles. Ne pas présenter les valeurs numériques ci-dessous comme des habitudes mesurées : elles sont des paramètres initiaux à calibrer. L'IA doit laisser l'utilisateur déroger à toute règle. Ce document ne constitue pas une analyse exhaustive de ses montages précédents.

## Intention
Film événementiel cinématographique haut de gamme, vivant, humain, élégant, centré sur l'émotion, la continuité narrative et le rythme. Éviter l'aspect « clip automatique », les effets kitsch, les jump cuts secs systématiques, les changements d'angle gratuits et les transitions tapageuses. Favoriser la cohérence visuelle, la musicalité et des plans qui respirent.

## Structure et durées
- Organiser selon les événements **effectivement présents** : préparatifs, lieux, arrivées, cérémonie/Houppa/Torah, signatures, portraits, réception, danse, conclusion. Ne jamais inventer une séquence absente.
- Deux modes : **film long** (préserver les moments et paroles significatifs) et **teaser/clip** (sélection narrative courte et musicale). La durée est demandée à l'utilisateur ou choisie comme paramètre modifiable ; aucune durée universelle.
- Ouverture : plan d'établissement ou détail fort, puis introduction progressive des personnes ; éviter un générique interminable.
- Fin : point émotionnel ou musical fort, résolution visuelle, puis fondu naturel si adapté.
- Référence disponible : teaser mairie Elie & Rebecca, 176 s, 49 changements de plan détectés (~3,6 s/plan en moyenne), mais ce **n'est pas une règle de durée**.

## Sélection des plans et coupes
- Priorité : moment significatif > lisibilité de l'action > netteté/exposition/cadrage > variété des angles.
- Écarter flou accidentel, recadrage raté, mouvement brutal ou doublon, **sauf** si la valeur émotionnelle justifie un choix manuel.
- Éviter de couper au milieu d'un geste, d'une phrase ou d'un regard important ; préserver des entrées et sorties de plan propres.
- Couper sur mouvement ou accent musical quand cela améliore la continuité, sans forcer chaque coupe sur un beat.
- Danse : rythme plus soutenu, alternance détails / visages / ensemble ; cérémonie : coupes plus espacées, priorité à l'émotion et aux paroles.
- **Paramètres de départ expérimentaux** : autoriser plans courts (<1 s) pour inserts, plans longs (>6 s) pour moments forts ; ne jamais imposer de seuil fixe global.
- L'option « Rythmer » doit ajuster le montage à la musique **sans** altérer les segments verrouillés, les discours protégés ou la synchro multicam.

## Multicam et drone
- Synchro hybride : audio en priorité si fiable, puis métadonnées, correspondances visuelles, repères manuels. Calculer et afficher un score de confiance ; ne pas inventer une synchro.
- Drone sans audio : chercher un événement commun (entrée, mouvement, flash, changement de lumière) ; vues aériennes et plans serrés peuvent rendre la correspondance automatique impossible. Proposer alors un alignement visuel manuel côte à côte, image par image, avec marqueurs.
- Préserver l'audio principal lors des changements d'angle. Priorités d'angles configurables : plan maître pour continuité, gros plans pour émotion, plans de coupe pour respirations.
- En 4K, recadrage 1080p possible avec limites de zoom et vérification de netteté ; ne pas inventer des gros plans de qualité si source insuffisante.
- Drone : établir le lieu, révéler l'échelle, créer des respirations et transitions de séquence ; ne pas en abuser ni l'insérer seulement parce qu'il existe.

## Audio et musique
- Séparer voix/discours, son direct, ambiance, musique. **Ne pas couper une prise de parole importante** sans décision explicite.
- Musique : morceau choisi par l'utilisateur, marquage des temps forts et phrases musicales, coupes au service de la narration ; privilégier la montée et la résolution plutôt qu'un beat-matching mécanique.
- Ducking de la musique sous les paroles, fondus doux, éviter saturation et variations brusques de volume.
- Respecter les droits d'utilisation des musiques et permettre le remplacement simple d'un titre.

## Transitions
- Par défaut : coupe propre, fondu enchaîné discret, fondu au noir si changement de chapitre. Les transitions de mouvement/masque ne sont proposées que lorsqu'elles sont motivées par les images.
- Durées modifiables ; vérifier les poignées de médias et la continuité audio. **Ne pas** ajouter une transition à chaque coupe.
- La timeline doit enregistrer les transitions de manière non destructive. Si leur rendu n'est pas encore implémenté (PR D), les signaler comme « modélisées, non rendues », sans laisser croire qu'elles apparaîtront à l'export.

## Étalonnage et rendu
- Look cinéma premium **naturel** : tons de peau crédibles, exposition maîtrisée, contrastes raffinés, cohérence entre Sony A7 IV, Osmo et drone.
- Pipeline colorimétrique explicite : reconnaître l'espace/courbe source (dont LOG), conversion vers espace de travail puis rendu Rec.709 ; distinguer **LOG (profil source)** et **LUT (transformation)**.
- LUTs importables, correction manuelle expo/WB/HL/ombres/contraste/saturation ; tout doit être réversible.
- N&B ponctuel et vignettage subtil appréciés, à employer avec intention, pas automatiquement sur toutes les images.
- Export 1080p ou 4K, cadence choisie selon rushes et rendu visé ; slow motion 50→25 fps si cohérent, pas de ralentissement systématique.

## Timeline et prompts
- Toute génération produit une **timeline native éditable**, jamais seulement un MP4.
- Manuel et IA éditent la même timeline : opération structurée, prévisualisation, diff, accepter/refuser, undo/redo, autosave, verrouillage de plans.
- Exemples : « privilégie caméra 2 pendant la Houppa », « conserve le discours », « raccourcis la danse de 20 s », « synchronise le drone à l'entrée », « harmonise les couleurs », « rythme les plans sur cette musique ».
- Ne jamais réinitialiser les corrections manuelles à la suite d'un prompt.

## Corrections V1 → V2 → V3 : catégories à anticiper
**Ce ne sont pas des fréquences observées ni une liste prouvée de demandes récurrentes.** Prévoir des outils simples pour : changer un angle, remplacer un plan flou, allonger une émotion, raccourcir une section, déplacer une coupe, ajuster le rythme, corriger une dominante colorée, doser une transition, préserver la voix, modifier une musique, revoir un recadrage.

## Tests et validation artistique
Pour chaque génération, afficher : séquences sélectionnées, plans exclus et raison, priorités caméras, score synchro, musique et marqueurs, changements proposés, alertes (audio absent, plan flou, transition non rendue). Comparer V1/V2 sur de vrais rushes avec validation humaine.

## À demander au réalisateur lors de la calibration
- Durées cibles par type de livraison (film long, teaser, clip) et exemples de 2–3 montages validés.
- Choix de musique, intensité des transitions, traitement du N&B, priorités d'angles.
- Rushes d'essai : 2–3 caméras avec audio, drone muet avec événement commun visible, prises discontinues, 4K50/25, WAV séparé.
- Collecter des retours concrets pour remplacer les paramètres expérimentaux par des préférences vérifiées.
