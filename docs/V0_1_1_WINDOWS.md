# Kolnoa Editor V0.1.1 — Stabilisation Windows

## Installer et démarrer

1. Décompresser entièrement `KolnoaEditor_V0.1.1.zip` sur une machine Windows x64.
2. Ouvrir `KolnoaEditor\KolnoaEditor.exe`. Laisser le dossier `_internal` à côté de l'EXE. Python, Qt, FFmpeg et FFprobe sont inclus.
3. Choisir le dossier de rushes. Kolnoa lit les originaux et enregistre le projet sous `Documents\Kolnoa_Projets`.
4. Cliquer sur **Monte-moi cette séquence**. Le bouton **Suivi de la tâche** permet de rouvrir la progression si elle a été réduite.

Le ZIP GitHub se trouve dans la section **Artifacts** de l'exécution « Kolnoa Editor V0.1.1 ». Le build portable n'est pas signé.

`KolnoaEditor.exe --diagnostic` active les traces détaillées. Le journal rotatif par défaut est
`%APPDATA%\KolnoaEditor\startup.log`. Les traces détaillées peuvent contenir des chemins de médias locaux.
`KolnoaEditor.exe --selftest` vérifie le parcours sur médias synthétiques et écrit le résultat dans ce journal.
`KolnoaEditor.exe --demo C:\Temp\KolnoaDemo` ouvre un projet de démonstration.

## Audit de la bêta Windows

| Périmètre | État observé dans la pile V0.1 |
| --- | --- |
| Lancement et distribution (#43) | Lanceur Qt, PyInstaller et autotest présents. L'EXE monofichier extrait son contenu avant d'entrer dans Python. L'ouverture, le scan et FFprobe bloquent ensuite le fil Qt avant que la fenêtre principale soit affichée. |
| Instance et mode résident (#44) | Verrou et réveil par connexion locale présents, avec des tests dédiés sur la branche. Icône de notification, export résident et réglages de démarrage appartiennent à ce brouillon distinct. Leur affichage sur le poste de montage reste à valider. |
| Rushes et montage | Scan et lecture des métadonnées, silences FFmpeg, planification et multicam par corrélation audio sont implémentés. L'analyse visuelle compare des échantillons d'angles. Le bouton appelle directement ce pipeline sur le fil Qt. |
| Timeline et persistance | Coupes, trim, sélection, consignes ciblées et journal de session sont implémentés. Chaque opération de génération réécrit les fichiers d'état dérivés et force la synchronisation du journal. Cela amplifie le coût sur une longue timeline. |
| Aperçu, couleur et transitions | Transport et extraction d'image présents. L'aperçu de la timeline principale reste sans son. Modèles de couleur et de transitions présents, rendu des effets partiel. |
| Export | Rendu et mixage MP4 existants. Plusieurs appels FFmpeg/FFprobe contournent le lanceur sans console. L'export complet est déclenché sur le fil Qt. |
| Moteur résident et Companion | Documentation et prototype résident dans la pile de branches. Le Companion et l'API restent hors de cette version. |

La bibliothèque du test contient **119 rushes Sony étiquetés A7IV, tous avec audio, pour 46 min 07 s de média**. À partir de leurs seuls horodatages, le moteur propose **8 groupes multicam**, impliquant 39 fichiers, dont un groupe de 18. Cette heuristique augmente le coût du montage même lorsque les médias proviennent d'une seule famille de caméras. Ces données expliquent le volume d'analyses et de synchronisations possibles. Elles ne permettent pas d'attribuer chaque groupe à des caméras physiques distinctes ni d'expliquer à elles seules le premier lancement invisible. PyInstaller monofichier peut aussi afficher un processus d'amorçage et un processus d'application pour un seul lancement.

La PR [#43](https://github.com/Kolnoaprod/kolnoa-editor/pull/43) fournit la base du build testé. La PR [#44](https://github.com/Kolnoaprod/kolnoa-editor/pull/44) fournit le verrou et le canal de réveil repris ici. `main` correspond encore à la base CLI. Les branches de la pile restent en revue.

## Changements V0.1.1

- **Démarrage.** Format portable en dossier, sélecteur visible, ouverture et import sur un worker, trace d'initialisation et de défaillance, mode diagnostic, verrou par utilisateur et réveil de l'instance existante.
- **Processus.** Drapeau Windows `CREATE_NO_WINDOW` pour les outils console. Délais bornés sur les sondes et les extractions courtes. Les analyses et encodages annulables attendent la sortie du processus avant de rendre la main. L'enveloppe du master audio est réutilisée pendant la synchronisation d'un groupe.
- **Réactivité.** Un import, montage ou export à la fois, avec suivi Qt et journal. L'édition est temporairement désactivée pendant ces opérations. L'extraction des images d'aperçu est asynchrone et limitée à une à la fois.
- **Progression.** Pour le montage, chaque rush compte une fois quand son audio a été évalué, puis une fois quand sa contribution à la timeline a été évaluée ou assemblée. Un dernier pas correspond à l'enregistrement. Les groupes multicam avancent à la fin du groupe, avec le détail des fichiers et des phases entre-temps. L'inventaire de l'import reste indéterminé jusqu'au comptage. L'export compte les segments terminés puis l'assemblage. Les pourcentages mesurent le travail accompli, sans extrapoler le temps restant.
- **Annulation.** La timeline et son historique sont publiés ensemble à la fin d'un montage réussi. L'annulation au milieu d'un montage ne laisse aucun clip partiel ni nouvelle action dans l'historique. L'export nettoie les fichiers partiels et préserve les segments de cache déjà valides. Après la publication du résultat, Ctrl+Z annule le montage en un geste.
- **Validation.** Tests Qt avec 5, 10 et 119 vidéos courtes synthétiques décodées par FFmpeg, tests de l'annulation et du nettoyage des processus, tests de fenêtre et autotest de l'EXE sur le parcours import, montage, modification, réouverture, export 1080p avec son et aperçu.

## Limites

L'heuristique de regroupement multicam reste celle de la V0.1. La lecture avec audio dans la timeline principale, la refonte de l'interface, le mode résident complet et le Companion ne font pas partie de ce chantier. Le comportement avec le GPU et les pilotes du poste de montage, la lecture temps réel 4K50 et les notifications système demandent des essais distincts.
