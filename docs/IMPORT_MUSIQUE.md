# Importation multimédia et analyse musicale

Ce lot expérimental ajoute un chutier de références à côté du projet historique. Le fichier `Medias.klnprj` conserve des chemins absolus et des identifiants. Les médias restent à leur emplacement. On peut importer des fichiers, des dossiers avec récursion facultative, et des bibliothèques musicales. Le glisser-déposer peut transmettre plusieurs dossiers. Le chutier distingue vidéo, audio, musique et photo.

Pour une musique du chutier, l'analyse estime un tempo et localise des battements à partir des variations d'énergie. Elle peut poser un morceau sur la piste de musique via le moteur existant et créer des repères sur la portion utilisée. Le tempo est déclaré incertain en l'absence de périodicité suffisante. L'estimation n'identifie pas les mesures, les sections, les transitions ni les effets narratifs.

Le menu **Enregistrer le projet .klnprj** crée un point d'entrée nommé. Il référence les données locales et le dossier historique. Le menu **Nouvelle séquence** crée une timeline et un historique indépendants. **Changer de séquence** permet de retrouver les montages, avec un chutier partagé. Pour rouvrir un projet nommé, passer son chemin à `KolnoaEditor.exe`. Un projet reste lié à son dossier de données, donc copier le manifeste seul ne duplique pas les montages.

Ce lot n'ajoute pas de reliaison des chemins, de moniteur source, de choix de plusieurs musiques, de déplacement de coupes par une commande **Rythmer** ni de ducking. Les repères sont éditables comme ceux de la timeline existante. Les protections temporelles et l'export des musiques entières comme règle éditoriale restent à développer.
