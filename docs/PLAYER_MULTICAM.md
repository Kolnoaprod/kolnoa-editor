# Lecteur multicam — choix techniques

Ordre de la revue : #9 → #13 → #14 → #16 → #18 → **cette PR** (empilée sur #18). Rien n'est fusionné.

## Architecture (`src/kolnoa_editor/player/`, sans Qt sauf `qt_backend.py`)
| Module | Rôle |
|---|---|
| `clock.py` | **Horloge maître** : point d'ancrage + `time.monotonic`, jamais incrémentée → aucune dérive (testé sur 3 h simulées). |
| `scheduler.py` | `plan(...)` fonction **pure** : pour chaque angle, rôle (ACTIVE / NEXT / WARM / PAUSED / COLD), qualité (source / proxy), priorité, coût. |
| `controller.py` | `MulticamPlayer` : un lecteur par angle asservi à l'horloge, bascule d'affichage « tardive », touches 1-9, prise. |
| `backend.py` | Contrat `AngleBackend` + `FakeBackend` déterministe (tests sans vidéo). |
| `qt_backend.py` | `QMediaPlayer` + `QVideoSink` ; `configure_hw()` (FFmpeg Qt : `QT_FFMPEG_DECODING_HW_DEVICE_TYPES`). |
| `ui/multicam_view.py` | Fenêtre : angle en grand + vignettes, touches 1-9, Espace, Maj+Espace = enregistrer, Entrée = valider, Échap = jeter. |

## Exigences → réponse
- **9 angles, touches 1-9** : `angle_for_key` du moteur (touche 1 = 1er angle image). Un angle sans média → refus clair, rien ne casse.
- **Horloge commune sans dérive** : les angles s'asservissent à l'horloge, jamais l'inverse. Écart < 20 ms (actif) / 40 ms (autres) : on ne touche à rien ;
  au-delà, vitesse ajustée de ±5 % max (invisible) ; > 250 ms : re-seek. Décalage/dérive d'angle via `Angle.to_angle` (modèle existant).
- **Décodage anticipé** : les coupes déjà enregistrées dans la prise (fenêtre de 3 s) passent en rôle NEXT (positionnées, synchronisées).
  Les autres angles sont tenus « chauds » en proxy → l'image est déjà là au moment de la touche.
- **Proxys** : angle actif en **source** seulement si le matériel décode (`hw_decode`) et que le budget tient aussi les autres en proxy ; sinon proxy.
  Hors budget : angles en PAUSED (ouverts, ré-alignés toutes les 0,5 s, coût ~0) — jamais l'actif. Le budget est en pixels/s (largeur × hauteur × cadence).
- **Priorité à l'actif** : rôle ACTIVE, priorité 0, qualité max, recalé en premier à chaque tick. *Qt n'a pas de priorité de décodeur* : la priorité est
  obtenue par le budget/les rôles, pas par un réglage du décodeur (limite connue).
- **Pas de gel** : l'affichage ne bascule que quand l'angle demandé a une image à ±80 ms ; entre-temps l'ancienne image reste. Si l'angle n'est pas prêt
  au bout de 0,75 s, bascule forcée et comptée (`stats.forced_swaps`) — mesure de la qualité du réglage.
- **Prise = opérations réversibles** : la prise vit en mémoire, `commit()` appelle `record_angle_switches` = **une Transaction** ; l'appelant fait `Session.do` ;
  `undo` annule toute la prise. Pendant la prise le modèle n'est pas modifié. Un angle non synchronisé annule toute la prise (règle du moteur).
- **Benchmarks séparés** (`tools/bench/bench_player.py`) : `seek` (setPosition → première image), `decode` (images/s à 4×, sans écran), `display`
  (conversion image + peinture, images sautées). `gen` fabrique 3 rushes 4K50 synthétiques. `hwcheck.py` : FFmpeg et Qt avec/sans GPU + colonne `dec` de nvidia-smi.

## Corrections de revue (ChatGPT)
- **Cadence de rafraîchissement = temps RÉEL, tolérance = temps MÉDIA.** Un angle « en pause » pendant la lecture est ré-aligné au plus toutes les 0,5 s *réelles*
  (le coût de décodage ne dépend pas de la vitesse de lecture) ; avant, la cadence suivait le temps média et explosait à 4×. Horloge en pause : anti-rafale 0,1 s réelles.
- **Seeks successifs / pauses** : chaque seek émis est « en vol » 0,6 s (ou jusqu'à l'atterrissage) ; pas de seek par-dessus un seek en vol ; un seek utilisateur écrase
  toujours le précédent (le dernier gagne). `FakeBackend(lag=True)` simule des seeks asynchrones (position qui n'arrive que plus tard). Tests : 3 seeks à 30 ms → tous les angles sur la dernière cible, pas de tempête de seeks.
- **Budget jamais dépassé en silence** : `plan()` renvoie un `Plan` (liste de `Slot` + `spent`, `budget`, `over_budget`, `missing_proxy`, `warnings`). Angle actif sans proxy dont la
  source dépasse le budget → dépassement **déclaré** ; angle non actif sans proxy et sans budget → PAUSED (jamais lu en 4K en continu) + avertissement. Le contrôleur expose
  `warnings`/`report` et compte `stats.over_budget_replans` (à afficher dans l'interface).

## Mesures du bac à sable (Linux, **sans GPU**, rendu logiciel — ne valent PAS pour la RTX A4500)
4K50 H.264 synthétique : seek médiane ~117 ms, p95 ~190 ms ; décodage logiciel 3 flux 4K à 4× : 64–76 i/s par flux (donc > 50 i/s : tient 3×4K50 en logiciel
ici) ; affichage : **`frame.toImage()` sur du 4K coûte ~22 ms (1 flux) à ~41 ms (3 flux) en CPU** — c'est le vrai coût, pas la peinture (0,6 ms).
Conséquence de conception : la grille de vignettes lit les **proxys** ; seul l'angle affiché en grand devrait passer par un chemin GPU (QVideoWidget /
rendu RHI) — à faire/mesurer sur Windows (le bac à sable n'a pas de RHI). Timeline : pan ~5 ms, zoom continu ~17 ms (test de régression : < 40 / < 60 ms).

## À valider sur la machine Windows (RTX A4500) — NON vérifié ici
1. `python tools/bench/hwcheck.py rush_4k50.mp4` → `hwcheck.json` (FFmpeg et Qt, avec/sans `cuda,d3d11va`, `dec` > 0 % = le GPU décode).
2. `python tools/bench/bench_player.py all rush1.mp4 rush2.mp4 rush3.mp4 --hw cuda,d3d11va --out bench_player.json` (vrais 4K50 ; HEVC 10 bits si c'est le format réel).
3. Si Qt ne décode pas sur GPU : plan B = décodeur dédié (PyAV/NVDEC) derrière le même `AngleBackend` — le contrat est prévu pour.

## Limites connues / suite
- L'audio n'est pas encore piloté par l'horloge (le son vient du maître, lecture audio = chantier du moteur audio) ; la lecture arrière n'est pas gérée.
- Pas de dessin des coupes de la prise sur la timeline (viendra avec trim/rasoir).
- Hors périmètre, ensuite : trim, rasoir, glisser depuis le chutier, formes d'onde asynchrones.
