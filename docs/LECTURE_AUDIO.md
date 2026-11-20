# Aperçu Windows — correctif de lecture et de son

Le précédent moniteur principal avançait la tête de lecture et demandait une image fixe à FFmpeg. Il ne jouait aucun son. La commande Lecture prépare maintenant une copie de travail 960 × 540 avec le mixage de la timeline, puis la lit en continu. Le rendu de préparation s'affiche dans la fenêtre de progression et reste annulable. Les segments de rendu peuvent être réutilisés et, tant que la timeline n'a pas changé, la commande Lecture réutilise la copie déjà préparée. Cliquer sur la timeline en pause continue de montrer une image fixe.

Le mixage multicam privilégie toujours la source audio désignée. Lorsque cette source est absente, muette ou hors de sa plage fiable, il choisit un angle synchronisé qui possède de l'audio et couvre le passage. Le journal de rendu indique le relais ou l'absence de source fiable. Les pistes muettes, solos et réglages de montage explicites restent appliqués. Les projets existants sont lus sans migration et leurs originaux restent référencés.

La copie légère impose une préparation lors de la première lecture d'un montage, puis après des modifications. La durée de préparation et la fluidité sur le poste de montage restent des points de validation en conditions réelles.
