# Moteur résident Windows et API Companion — étude d'architecture (proposition)

Statut : **étude, aucun code**. Lue : `Kolnoaprod/kolnoa-editor-companion` (README, `docs/API_CONTRACT_V0_1.md`, `docs/WINDOWS_AGENT_AND_EXPORTS.md`).
Périmètre Editor : moteur Windows + API. Périmètre Companion (ChatGPT) : application Android. Rien ici ne retarde la V0.1 (#43).

## 1. Ce que le moteur sait déjà faire (réutilisable tel quel)
| Besoin Companion | Existant dans Editor | Écart |
|---|---|---|
| Projets et état | `ProjectController.open`, `Project`, bibliothèque de rushes | Pas de registre multi-projets : à créer (liste de dossiers déclarés). |
| Timeline en lecture | `Timeline` sérialisable (`current.json`), `fps` entier | **fps rationnel** attendu par le contrat (ex. 30000/1001) : le moteur n'a que des entiers. |
| Révision de projet | Journal `history.jsonl`, instantanés | **Pas de compteur de révision** : à ajouter (= nombre d'étapes du journal + branche). Simple. |
| Commandes manuelles | `trim_head/tail`, `ripple_trim`, `SplitClip`, `MoveClip`, `switch_angle` : transactions atomiques annulables | Mapper `trim/split/move/switch_angle` du contrat sur ces builders. |
| Prompt ciblé | `parse_directive`/`apply_directive`, `Selection` (clip, plage, **tête de lecture** — #42), refus si portée absente | Déjà conforme : « aucun prompt ambigu ne modifie tout le projet ». |
| Annuler / rétablir | `Session.undo/redo`, `undo_group` | Exposer `undo_token`. |
| Protection du travail manuel | Provenance + `Scope` + refus des plans `valide` | Rien à faire. |
| Aperçus | Proxys (`proxies.py`), `render_timeline(kind="proxy")`, cache par segment | Rendre un **proxy de la révision courante** + étiquette de révision ; segmentation HLS/MP4 à étudier. |
| Exports | `render_timeline` 4K/1080p, `RenderReport`, annulation (`CancelToken`) | **Pas de file d'attente ni de journal de jobs** ; progression par segment existe (`on_progress`). |
| Analyses | `analyze`, analyse visuelle, `AnalysisStats` | Idem : à envelopper dans des jobs. |

Aucun serveur, aucune authentification, aucun gestionnaire de tâches n'existe aujourd'hui : c'est le vrai travail.

## 2. Architecture proposée
```
KolnoaEditor.exe (un seul processus Windows, session utilisateur, sans droits élevés)
 ├─ Moteur (Python pur, SANS Qt)  ← source de vérité
 │   ├─ ProjectRegistry   : projets déclarés, un ProjectController par projet ouvert
 │   ├─ ProjectActor      : une file de commandes SÉRIALISÉE par projet (verrou unique d'écriture), contrôle de révision, idempotence
 │   ├─ JobManager        : file de jobs (analyse, montage, aperçu, export) — threads/processus ffmpeg, annulation, pause, état persistant (jobs.jsonl)
 │   └─ Delivery          : destinations préautorisées (dossier local, dossier Drive synchronisé), vérif d'intégrité, statuts rendu/transfert séparés
 ├─ API Companion (HTTP + WebSocket, hors Qt) : /api/companion/v1, authentification d'appareil, journal d'audit
 └─ Interface Qt (optionnelle) : fenêtre AppWindow + QSystemTrayIcon ; le même processus, mais le moteur survit à la fermeture de la fenêtre
```
Décisions :
1. **Un seul processus** (le tray n'est qu'une vue). La fermeture de la fenêtre masque ; seul « Quitter » arrête le moteur (confirmation s'il reste des jobs).
2. **Moteur sans Qt** : la fenêtre actuelle appelle déjà `ProjectController` ; on déplace seulement les threads/jobs hors de l'UI. Test : le moteur démarre en ligne de commande sans importer PySide6.
3. **Serveur** : ASGI léger (FastAPI/uvicorn ou équivalent, à valider : poids dans l'EXE) ; sinon `http.server` + WebSocket minimal. Choix après mesure de la taille du PyInstaller.
4. **Écriture unique** : toute mutation passe par `ProjectActor` ; la fenêtre Qt et Companion sont deux clients du même acteur (une seule timeline, une seule Session — principe déjà acquis).
5. **Companion ne touche jamais aux fichiers** : il n'envoie que des commandes ; chemins de rushes jamais exposés, destinations par identifiant.

## 3. Contrat : points à aligner avec ChatGPT (ouverts)
1. **Révision** : entier croissant par projet, incrémenté à chaque étape de journal ; un undo produit une nouvelle révision (pas de retour en arrière du compteur).
2. **fps rationnel** : l'API expose `{num,den}` ; en interne Editor reste entier pour la V0.1 → pour 29,97/59,94 il faut étendre le modèle (`Timeline.fps` entier utilisé partout). **Risque réel**, à traiter avant d'annoncer le support des cadences NTSC. Les rushes Sony A7 IV 25/50 i/s ne sont pas concernés.
3. **Types de commandes** : `trim` = `{edge: head|tail, delta_frames}`, `split` = `{at_frame}`, `move` = `{to_start, to_track}`, `switch_angle` = `{angle_id}`, `prompt` = `{text}` + portée obligatoire, `undo`/`redo`. Ajouter `set_speed` (#41) plus tard. Les refus du moteur (chevauchement, plan validé, piste verrouillée) → `rejected` avec message lisible, jamais un 500.
4. **Idempotence** : table `command_id → résultat` persistée avec le journal du projet (même ID = même résultat, sans ré-exécution) ; idem `request_id` des exports.
5. **Conflit** : 409 + révision courante + (option) liste des plans modifiés depuis `expected_revision`.
6. **Aperçus** : un proxy de la révision R n'est valable que pour R ; Companion demande `previews?revision=R`. V0.1 : MP4 proxy 540p complet puis streaming par plages HTTP (`Range`) ; HLS seulement si les essais sur appareil le justifient.
7. **Événements** : WebSocket authentifié ; reprise par `GET` (comme prévu). Numéroter les événements (`seq`) pour détecter les trous.
8. **Authentification** : appairage code à usage unique + confirmation **sur Windows** (boîte de dialogue du tray) → jeton d'appareil révocable stocké **haché** côté PC ; limitation de tentatives ; journal d'audit. LAN en HTTPS avec certificat auto-signé épinglé à l'appairage (QR = empreinte du certificat). Accès Internet : **ne pas ouvrir de port** ; passer plus tard par un tunnel sortant chiffré (à étudier, **sans service payant** — budget 0 €) ; hors V0.1.
9. **Exports** : presets fixés côté PC (`mp4_1080p`, `mp4_4k`), destinations déclarées sur le PC ; nom de fichier validé ; jamais d'écrasement silencieux ; rendu et livraison = deux statuts. Drive : **dossier synchronisé localement** d'abord (aucune API Google, aucun jeton à stocker) ; l'API Drive directe seulement plus tard et après validation.

## 4. Agent résident (point 1 de la demande)
- `QSystemTrayIcon` : menu *Ouvrir · Tâches en cours · Pause/reprise · Statut Companion · Démarrer avec Windows · Quitter*.
- Fermeture de fenêtre → masquage + notification « Kolnoa Editor continue en arrière-plan » (réglable).
- Démarrage avec Windows : clé `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (par utilisateur, sans élévation), option « démarrer réduit » (`--minimized`). Désactivation = suppression de la clé.
- Jobs persistés : au redémarrage, ceux « en cours » repassent en « interrompu — à relancer » (pas de reprise automatique d'un rendu lourd sans politique).
- Limites à dire clairement : pas de travail PC en veille ; un PC verrouillé continue, une session fermée non (ce n'est pas un service).

## 5. Découpage proposé (PR brouillons, après la V0.1)
| Lot | Contenu | Dépend de | Effort |
|---|---|---|---|
| **M1** | `JobManager` + `jobs.jsonl` + annulation/progression ; l'export de la fenêtre l'utilise | V0.1 | moyen |
| **M2** | Révision de projet + `ProjectActor` (sérialisation, idempotence, conflit) + mapping des commandes | M1 | moyen |
| **M3** | Tray + fermeture en arrière-plan + démarrage avec Windows + « Quitter » | V0.1 | **rapide** |
| **M4** | Serveur API lecture seule (machine, projets, timeline, jobs, aperçus) + authentification/appairage | M2 | moyen–long |
| **M5** | API écriture (commandes) + WebSocket événements | M4 | moyen |
| **M6** | Exports distants : presets, destinations préautorisées, file, livraison locale puis Drive synchronisé | M1, M4 | moyen |
| **M7** | Accès Internet sécurisé (tunnel sortant), comptes multiples | M4–M6 | long, hors V0.1 |

**Rapide après la bêta** : M3 (tray/arrière-plan), M1 (jobs), lecture seule de M4 sur le LAN.
**Plus long** : appairage sécurisé complet avec TLS épinglé, aperçus fluides sur Android, fps rationnel, Internet.
**À ne pas promettre** : montage sur PC éteint/en veille ; validation Android réelle sans appareil réel.

## 6. Règles de coordination Editor ↔ Companion
- Le contrat vit dans un seul dépôt (proposition : `kolnoa-editor-companion/docs/API_CONTRACT_V0_1.md` fait foi) ; toute modification passe par une PR commentée par l'autre partie.
- Editor fournira un **serveur de test** (moteur + données synthétiques) pour que Companion développe sans PC réel.
- Aucun développement serveur ne démarre avant la validation de la V0.1 ; aucune fusion sans accord du réalisateur.
