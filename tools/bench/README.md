# Essai de performance : Qt ou web local ?

But : décider de la boîte à outils de l'interface (voir `docs/GUI_COLOR_SPEC.md`, §6) avec des chiffres mesurés sur **ton** PC, pas des suppositions. Les deux essais ne modifient aucun fichier et n'envoient rien.

**Web (rien à installer)** : ouvrir `bench_web.html` dans Chrome ou Edge, choisir 3 vidéos (une par caméra, proxys ou rushes courts), cliquer « Lancer l'essai », copier le résultat.

**Qt** : `pip install PySide6` puis `python tools\bench\bench_qt.py cam1.mp4 cam2.mp4 cam3.mp4`.

Lecture des résultats :
- timeline de 800 plans : médiane sous ~8 ms et p95 sous ~16 ms = défilement fluide ;
- changement d'angle (seek) : médiane sous ~100 ms = le jeu des touches 1 à 9 reste jouable en lecture ;
- lecture simultanée de 3 angles : images perdues proches de 0.

Limites : ce n'est pas le rendu final (pas de décodage de rushes 4K 10 bits LOG, pas de waveforms) ; essayer aussi avec un rush 4K réel si possible.
