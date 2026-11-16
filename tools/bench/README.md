# Essai Qt / web local

## Le plus simple : KolnoaBenchmark.exe

1. Double-clique sur `KolnoaBenchmark.exe` (au premier lancement Windows peut afficher « Windows a protégé votre ordinateur » :
   clique sur *Informations complémentaires* puis *Exécuter quand même* ; le programme n'est pas signé).
2. **Choisir 3 vidéos** (tes 3 caméras), puis **Lancer le test**.
3. Ne touche à rien : des fenêtres s'ouvrent et se ferment seules (3 tours × Qt/web × copies légères/vidéos originales).
4. À la fin, le dossier s'ouvre : envoie `REPORT.md` (dossier `Documents\Kolnoa_Benchmark\<date>`).

Rien n'est installé, rien n'est envoyé sur Internet, les vidéos ne sont jamais modifiées. Edge ou Chrome doit être présent (il l'est sur Windows).
La première fois, la création des copies légères prend quelques minutes (elles sont réutilisées dans le même dossier).

---

## Version ligne de commande (avancé)

# Essai Qt (PySide6) ou web local : protocole reproductible

Objectif : **mesurer, sur le PC Windows du réalisateur**, avec les **mêmes médias** et la **même timeline**, ce que valent PySide6/Qt et une interface web locale pour Kolnoa Editor. La décision n'est prise qu'après lecture des résultats ; la préférence initiale est Qt/PySide6 (décision ChatGPT, PR #14), à confirmer ou infirmer par les chiffres. **Aucun chiffre n'est estimé** : ce qui n'a pas été mesuré s'affiche « non mesuré ».

## Lancer (Windows : environ 5 minutes de préparation, 10 à 20 minutes d'essai)
1. Prérequis : Python 3.10+, `ffmpeg`/`ffprobe` dans le PATH, Edge ou Chrome. Le script installe `psutil`, `numpy`, `PySide6` pour l'utilisateur courant.
2. Choisir **3 vidéos** (une par caméra), idéalement des rushes réels 4K 10 bits d'au moins 30 s. Un enregistreur audio peut servir aux formes d'onde (`--audio`).
3. Dans une invite de commandes, à la racine du dépôt :
   ```
   tools\bench\run_bench.bat "D:\rushes\cam1.mp4" "D:\rushes\cam2.mp4" "D:\rushes\cam3.mp4"
   ```
4. Ne rien toucher pendant l'essai : les fenêtres Qt puis web s'ouvrent seules et doivent rester au premier plan. Ne rien lancer de lourd à côté.
5. Envoyer `%USERPROFILE%\kolnoa_bench\REPORT.md` (et `results.json`). Relancer **2 fois de plus** : on compare les trois séries.

Les médias ne sont jamais modifiés : proxys, pics audio et résultats sont écrits dans `kolnoa_bench`. Le serveur web écoute `127.0.0.1` seulement ; rien n'est envoyé sur internet.

Options : `--only qt` / `--only web`, `--variants proxy`, `--browser chrome`, `--play-seconds 30`, `--dry-run` (prépare proxys et scénario sans ouvrir d'interface), `--no-audio` (machine sans carte son), `--browser-arg=…`.

## Ce qui est comparé (identique pour les deux)
| Mesure | Comment |
|---|---|
| Lancement | secondes entre le lancement et la 1ʳᵉ image affichée |
| **Décodage seul** | 3 angles lus en parallèle **sans** interface : images/s décodées (coût du codec) |
| Lecture avec interface | même lecture + timeline de 800 plans, 2 pistes vidéo + **8 pistes audio avec formes d'onde**, tête de lecture à 30 Hz : images/s présentées |
| Lecture / pause | latence (ms), 20 allers-retours |
| Scrub | délai jusqu'à l'image (ms), 40 positions |
| **Touches 1-9 en lecture** | délai jusqu'à l'image du nouvel angle : (A) angles décodés en parallèle, (B) un seul lecteur qui change de source |
| Zoom / déplacement de timeline | temps de redessin, 300 images de zoom + défilement |
| Sélection I/O, glisser-déposer | redessin pendant 30 glissements simulés de 20 pas |
| CPU / RAM / GPU | échantillonnés toutes les 0,5 s par phase ; somme des processus de l'essai (Qt) ou du navigateur à profil dédié (web) ; 100 % = un cœur ; GPU via `nvidia-smi` si présent |
| Stabilité | erreurs de lecteur et délais dépassés listés dans le rapport |

Chaque mesure : **médiane / p95 / max** et nombre d'échantillons. Deux variantes : **proxys** (H.264 540p, image clé chaque seconde : l'aperçu fluide visé) et **médias source** (pleine qualité, pour voir si le décodeur suit). Le **coût de décodage** est séparé du **coût de l'interface** : à lire ensemble avant de conclure.

## Limites assumées
- Une seule machine ; trois lancements pour estimer la dispersion.
- Le décodage des rushes source (4K 10 bits, HEVC…) dépend du codec disponible : une erreur de lecture est un résultat, pas un bug de l'essai.
- Le temps de peinture mesuré est celui du CPU ; la présentation GPU n'est observable qu'indirectement.
- Un prototype ne prouve ni la facilité de développement, ni les tests, ni la maintenance de l'interface complète.

## À évaluer à part (emballage Windows, **non mesuré par le script**)
Taille d'un paquet PyInstaller avec PySide6 ; démarrage à froid du paquet ; signature et antivirus ; installeur et mise à jour ; pour le web local : moteur (WebView2 ou navigateur de l'utilisateur), dépendance à un navigateur, accès aux fichiers locaux.
