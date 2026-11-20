# Audit du montage automatique V2

- Le scanner conserve des chemins relatifs, mais `SourceFile.group` reprend le premier dossier et l'interface l'appelle « séquence ». Le contrôleur utilise également cette valeur pour borner les propositions multicam. Deux caméras rangées dans deux dossiers racine ne sont donc jamais comparées.
- Le regroupement existant utilise des chevauchements d'horodatages et des grappes transitives. Sur le corpus mono-étiquette A7IV, il a proposé huit groupes. L'horodatage doit servir à proposer des candidats et la corrélation audio à les confirmer.
- `analyze_angle` mesure la netteté et les extrêmes de luminance par échantillon. Le montage à plat ne l'utilise pas. Le planificateur découpe les plages sonores suivant leur longueur et utilise un début de rush en repli. Aucun classement visuel par plage ne participe à ce parcours.
- Le modèle d'angle dispose déjà du décalage, de la confiance, de la dérive et de la plage de validité. La synchronisation audio, la timeline éditable, le journal transactionnel et le worker Qt sont réutilisables.
- Les annotations de source et les séquences éditoriales indépendantes ne sont pas modélisées dans ce parcours. Les consignes de timeline et sa provenance protègent des clips déjà montés, ce qui est distinct des annotations d'un rush avant montage.
- Le premier lot introduit un périmètre explicite de médias pour les propositions multicam et la sélection technique par plages. Les seuils sont expérimentaux. La stabilité, le mouvement, les doublons entre sources, les moments narratifs, le timecode fiable et les séquences éditoriales persistantes demandent des travaux dédiés.

## Parcours expérimental

Le bouton **Film condensé (essai)** sélectionne des médias issus de plusieurs dossiers et permet de désactiver la synchronisation audio. Les propositions inter-dossiers utilisent les horodatages, puis le moteur audio existant décide si un angle peut être utilisé. Les angles incertains restent disponibles dans le projet. Le montage à plat évalue des fenêtres successives et choisit un extrait par plage exploitable, à durée libre. La qualité et le rythme multicam suivent encore le moteur existant. Le journal expose les critères techniques et les nombres de plages retenues ou rejetées. Le montage classique reste accessible par le bouton habituel.

Ce lot est une première tranche de la V2. La durée personnalisée, les priorités d'angle, les niveaux Strict/Souple, les protections manuelles de plages, le timecode et la persistance de plusieurs séquences restent à implémenter. Les tests synthétiques contrôlent les règles élémentaires de sélection et de regroupement. Le calibrage sur des rushes réels reste à faire.
