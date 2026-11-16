# Tâches en arrière-plan (PR C1a)

Couche commune aux analyses longues (calibration, synchro, plus tard rendu et prompts). Elle ne dépend d'aucune interface : l'onglet Synchro (C1b) comme la ligne de commande n'utilisent que ce qui suit.

## Utilisation
```python
from kolnoa_editor.tasks import TaskRunner

runner = TaskRunner()                       # une tâche à la fois par défaut
runner.add_listener(lambda ev: ...)         # état, progression, journal ; appelé depuis le fil de travail
task = runner.submit("synchro", lambda ctx: calibrate.run(opts, progress=ctx.report, cancel=ctx.cancel))
task.cancel()                               # arrête aussi le processus ffmpeg en cours
task.state, task.progress, task.result, task.error, task.logs
```
- **États** : en attente, en cours, terminée, échouée, annulée. Une tâche annulée avant son démarrage ne lance rien. Une tâche annulée n'est jamais présentée comme réussie, même si sa fonction ignore le jeton.
- **Progression** : `ctx.report(étape, courant, total, message)` ; fraction et temps restant estimé par étape ; au plus ~20 mises à jour par seconde (la dernière d'une étape est toujours émise).
- **Erreurs** : message, exception d'origine et trace conservés ; une exception dans un listener ne perturbe jamais la tâche.
- **Fils** : l'interface doit ramener les événements sur son propre fil (signal Qt, file d'événements…).

## Dans `sync-calibrate`
La commande passe par ce même chemin : progression sur la sortie d'erreur (`[audio] 2/6, reste ~40 s`), Ctrl+C = annulation propre (code 130), aucun rapport partiel écrit, rushes jamais touchés. `--quiet` ne garde que progression et résultat. Une annulation n'est jamais comptée comme « erreur de fichier ».

## Pas encore fait (C1b)
Onglet graphique : dépend du choix de la boîte à outils (essai de performance à lancer sur le PC).
