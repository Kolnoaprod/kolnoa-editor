# Application Kolnoa Editor — première coquille (étape 1)

Lancer : `python -m kolnoa_editor.ui.app_window [dossier de rushes]` (PySide6). Branche `feat/app-shell`, empilée sur le monteur v0.

| Besoin | État |
|---|---|
| Importer / organiser les rushes (par séquence = dossier) | ✅ import en lecture seule, liste par séquence |
| Timeline visuelle et interactive | ✅ composant existant (déplacer, rogner, rasoir, formes d'onde, annuler/rétablir) |
| Sélection de plans et de zones, points d'entrée/sortie | ✅ clic = plans ; **I / O** = zone (affichée en bleu) ; la consigne agit sur la zone, sinon sur les plans |
| Panneau de commandes par prompts | ✅ ligne de commande + bouton « Monte-moi cette séquence » ; erreurs affichées, jamais silencieuses |
| Sauvegarde / réouverture | ✅ journal à chaque geste + instantané (Ctrl+S) ; rouvre à l'identique (testé) |
| Export MP4 | 🔌 branché sur un `exporter` injectable ; le rendu réel est la PR J1-b (#35) — liaison à faire à la fusion |
| Lecteur vidéo et multicam intégrés | ⏳ composants existants (`MulticamView`, `MulticamPlayer`) non encore intégrés à cette fenêtre : lot suivant |
| Prévisualisation du rendu | ⏳ dépend du lecteur + J1 |

Tests : fenêtre pilotée hors écran (liste de rushes, génération, zone I/O → consigne → annulation, sélection, erreurs, enregistrer/rouvrir, export).
Non mesuré sur le PC de montage (fluidité, 4K50) : aucune mesure n'a été faite sur la vraie machine.
