# Icône de notification, arrière-plan et instance unique (M3)

Branche `feat/tray-background`, PR brouillon indépendante, base `feat/v0.1-windows` (#43, non modifiée). Aucune fusion sans accord.

## Ce que ça fait
- **Icône Kolnoa près de l'horloge** (`ui/tray.py`) : clic = ouvrir ; menu = *Ouvrir l'éditeur · Tâches en cours (avancement, Annuler) · Démarrer avec Windows · Continuer en arrière-plan à la fermeture · Quitter Kolnoa Editor*.
- **Fermer la fenêtre la masque** (réglable) : le moteur, les tâches et l'export en cours continuent ; message unique « continue en arrière-plan ». **Quitter** (icône) annule les tâches (après confirmation s'il y en a), libère le verrou et arrête tout.
- **Démarrage avec Windows** : *Fichier › Paramètres…* ou menu de l'icône ; clé `HKCU\\…\\Run` (par utilisateur, sans droits élevés) ; option « démarrer réduit » (`--minimized` : icône seule, projet précédent rouvert s'il existe). Jamais activé sans demande.
- **Export en tâche de fond** : avec le moteur, *Exporter en MP4* ne bloque plus la fenêtre ; progression et annulation dans le menu de l'icône ; notification à la fin si la fenêtre est masquée.
- **Instance unique** : verrou de fichier (`engine.lock`, libéré par le système même en cas de plantage) + `QLocalServer` : un second lancement réveille la première fenêtre puis se termine.

## Architecture (pour Companion)
`app/engine.py` `ResidentEngine` (sans Qt) = verrou, `AppSettings`, `JobRegistry`, `controllers` (projets ouverts), `status()` (futur `GET /machine`), `api_endpoint` (futur serveur). Qt (fenêtre, icône) n'en est qu'une vue. `JobRegistry` (`app/jobs.py`) : tâches thread-safe, observables, annulables, historique `jobs.jsonl` — base de M1/M6. **Pas de serveur ni d'API dans ce lot.**

## Limites
- Un job « en cours » à l'arrêt brutal n'est pas repris (il n'est pas non plus écrit comme terminé) ; la reprise contrôlée viendra avec M1.
- Seul l'export est une tâche de fond ; analyse/montage restent synchrones dans cette PR.
- Pas de service Windows : session fermée ou PC en veille = pas de travail.
- Icône : dessinée par le code (pas d'asset de marque) ; remplaçable.
- Ni le zone de notification réelle ni le démarrage automatique réel n'ont été vus à l'écran : testés par la CI (registre réel sur runner Windows, deux instances de l'EXE) et par des tests Qt hors écran.
