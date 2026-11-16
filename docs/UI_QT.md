# Interface Qt (PySide6) — choix techniques

Décision du réalisateur après le benchmark Windows : interface **PySide6/Qt**, pas de version web (voir PR #15).
Cette PR (étape 1) ne contient que la **timeline** ; les étapes suivantes sont listées plus bas. Rien n'est fusionné.

## Principes
- **Le widget ne possède aucune donnée** : il lit `Session.tl` et ne la modifie que par les opérations du moteur
  (`move_clips` → `Session.do`). Un glisser = une étape d'annulation ; un refus du moteur (chevauchement, piste verrouillée)
  laisse le modèle strictement inchangé et émet `rejected`.
- **Géométrie sans Qt** (`ui/layout.py`) : image↔pixel, zoom ancré sur le curseur, rangées, plans visibles (index trié +
  dichotomie), hit-test, graduations. Testable sans écran.
- **Formes d'onde** (`ui/peaks.py`) : pyramide de crêtes (20 ms au niveau 0, maximum sur 2^k au-dessus), colonnes de pixels
  calculées en numpy, posées en une image. Les images des plans entièrement visibles sont mises en cache (le défilement ne
  les recalcule pas ; le zoom les invalide).
- **Tête de lecture** : ne redessine que ses deux bandes.

## Mesures (bac à sable Linux, rendu hors écran logiciel — PAS le PC de montage)
800 plans, 10 pistes, 222 plans visibles, 1200 px : ~4–5 ms par image au défilement, ~17 ms par image pendant un zoom continu
(tout est recalculé). Point de comparaison du benchmark Windows (Qt, version naïve) : 8 ms au pan/zoom. À re-mesurer sur le PC de
montage avec le même scénario avant de conclure ; le zoom est le cas à surveiller.

## Hors périmètre de cette étape (à venir, PR séparées)
trim par les bords, rasoir et touches clavier, plusieurs pistes visibles/repliables, lecteur multicam parallèle (touches 1-9),
glisser depuis le chutier, mixeur audio, chargement des crêtes en tâche de fond (`load_peaks` existe, non branché),
décodage matériel à vérifier.
