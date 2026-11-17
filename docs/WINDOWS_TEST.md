# Kolnoa Editor — essai sur Windows (RTX A4500) et mesures sur les vrais rushes 4K50

Rien ici ne modifie tes rushes (lecture seule). Les proxys, la session de montage et les mesures vont dans `Documents\Kolnoa_Test\<date>`.
Le dépôt est public : **n'envoie/ne colle jamais de noms de fichiers clients** (les rapports écrivent « rush 1 », « rush 2 »…).

## A. Le plus simple : l'application d'essai (zip)
1. Télécharge `KolnoaEditorTest.zip` (page « Releases » du dépôt → pré-version « Kolnoa Editor — application d'essai Windows »), dézippe, double-clic sur **KolnoaEditorTest.exe**
   (non signé : SmartScreen peut avertir → « Informations complémentaires » → « Exécuter quand même »).
2. Choisis **2 à 9 vidéos** (caméras d'un même événement, idéalement des 4K50). Le programme prépare des proxys légers (début des rushes, 90 s) puis ouvre la fenêtre.
3. À essayer (les angles sont calés à zéro : c'est un banc d'essai du ressenti, pas de la synchro) :
   - **Espace** lecture/pause · **1 à 9** change d'angle · **Maj+Espace** enregistre une prise · **Entrée** la valide (annulable d'un geste) · Échap la jette.
   - Timeline : clic sur la règle = tête de lecture · glisser un plan = déplacer · **tirer un bord = rogner** (aimantation, butée « limite ») · **Alt + tirer = ripple** ·
     **S** = rasoir à la tête de lecture (Maj+S = toutes les pistes) · **Ctrl+Z / Ctrl+Maj+Z** · glisser un plan du chutier (liste de gauche) sur une piste
     (Ctrl pendant le glisser = insertion au lieu du recouvrement) · Ctrl+molette = zoom.
4. Bouton **« Mesurer (4K50, GPU)… »** : lance seek / décodage / affichage, d'abord en logiciel puis avec `cuda, d3d11va`, en lisant le moteur de décodage du GPU
   (`nvidia-smi`). Compter quelques minutes, ne rien toucher. Résultat : `Documents\Kolnoa_Test\<date>\mesures\MESURES.md` et `mesures.json` → **envoie-les à Claude**.

## B. Les mêmes mesures en ligne de commande (PowerShell, depuis le dossier du dépôt)
```powershell
pip install PySide6 numpy
$env:PYTHONPATH = "src"
# 0) (option) fabriquer 3 rushes synthétiques 4K50 de 10 s pour un premier essai :
python tools\bench\bench_player.py gen C:\Temp\synth4k50
# 1) décodage matériel : FFmpeg et Qt, avec/sans GPU, + moteur de décodage GPU (nvidia-smi). Ton vrai rush 4K50 :
python tools\bench\hwcheck.py "D:\rushes\rush_4k50.mp4"
# 2) bancs séparés seek / décodage / affichage, logiciel puis GPU :
python tools\bench\bench_player.py all "D:\rushes\a.mp4" "D:\rushes\b.mp4" "D:\rushes\c.mp4" --out bench_logiciel.json
python tools\bench\bench_player.py all "D:\rushes\a.mp4" "D:\rushes\b.mp4" "D:\rushes\c.mp4" --hw cuda,d3d11va --out bench_gpu.json
# 3) tout d'un coup, avec rapport lisible (Mesures.md + mesures.json) :
python -c "from kolnoa_editor.player.suite import run_suite; run_suite([r'D:\rushes\a.mp4', r'D:\rushes\b.mp4', r'D:\rushes\c.mp4'], r'C:\Temp\mesures')"
```
Avec l'EXE (sans installer Python) : `KolnoaEditorTest.exe --player-worker all a.mp4 b.mp4 c.mp4 --hw cuda,d3d11va --out bench_gpu.json` (pas de fenêtre ; le fichier JSON est écrit).

## C. Comment lire les résultats
- **Décodage matériel** : pendant les mesures « gpu », la colonne `dec` de `nvidia-smi` doit monter nettement (> 10 %) alors qu'elle reste ~0 en « logiciel ».
  Sinon le verdict dit « PAS de preuve » → plan B (décodeur NVDEC dédié derrière le même contrat de lecteur d'angle).
- **seek** : médiane/p95 en ms par rush (un seul lecteur) ; le multicam en parallèle vise ~10 ms de bascule (angles déjà chauds).
- **décodage** : images/s à 4× pour 1, 2, 3 flux ; pour du 4K50 il faut > 50 par flux à 1× (donc beaucoup plus à 4×) pour tenir N flux en temps réel.
- **affichage** : coût de conversion d'une image 4K + peinture ; en logiciel ~20-40 ms mesuré dans le bac à sable → grille en proxys, grand angle en chemin GPU.
- Dans la fenêtre : la barre d'état montre l'angle affiché, la latence de bascule, les re-seeks, et ⚠ si le budget de décodage est dépassé ou si un proxy manque.

## D. Ce que je dois te renvoyer
`MESURES.md` + `mesures.json` (ou `hwcheck.json` + `bench_*.json`), et ton ressenti : le zoom continu est-il fluide ? Le glisser-déposer au vrai pointeur ? Les bascules d'angle ?
Les chiffres du bac à sable Linux (sans GPU) ne valent pas pour ta machine : seuls ceux-là décident du budget du planificateur.
