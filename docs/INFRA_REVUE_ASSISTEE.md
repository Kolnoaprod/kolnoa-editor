# Infrastructure de revue assistée (issue #30) — PR de configuration INACTIVE

**Statut : documentation + modèle inerte. Rien n'est activé, aucun secret n'est lu, aucun workflow n'existe dans `.github/workflows/`.**

## 1. Décision du réalisateur (issue #30, 17/11) — périmètre final
- **Budget additionnel : 0 €, définitivement**, même avec autorisation ultérieure. Aucune option payante n'est proposée.
- Exclus : `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, crédits, abonnement, facturation à l'usage, boucle Claude ↔ OpenAI, `openai/codex-action`.
- Le jeton `CLAUDE_CODE_OAUTH_TOKEN` déjà enregistré **ne doit être utilisé dans aucun workflow** tant que l'absence de frais n'est pas établie (§4).
- Aucune fusion automatique, aucun déploiement, aucun accès aux médias clients, aucune autorisation implicite pour A2/E/F/G/M1–M10.
- En attendant : **revues manuelles de ChatGPT sur GitHub**, comme aujourd'hui.

## 2. Ce que contient cette PR
| Fichier | Rôle |
|---|---|
| `docs/INFRA_REVUE_ASSISTEE.md` | ce document : constats de coût, protocole de vérification, arrêt d'urgence |
| `infra/inactive/claude-sur-commande.yml.modele` | modèle d'un workflow manuel (extension `.modele` : GitHub ne le lit pas) ; contient une étape qui échoue volontairement |
| `tests/test_infra_inactive.py` | garde-fou : échoue si un workflow actif référence Claude/OpenAI/un secret d'IA, `pull_request_target` ou un runner « larger » |

## 3. Coûts — ce qui est vérifié, ce qui ne l'est pas
| Élément | Constat | Fiabilité |
|---|---|---|
| Minutes GitHub Actions, runners **standard**, dépôt **public** (le dépôt est public) | **gratuites** ; les runners « larger » sont payants même en public | doc officielle GitHub (« About billing for GitHub Actions ») |
| Clé API OpenAI / Anthropic | facturation à l'usage | exclue du périmètre |
| `claude-code-action` avec `CLAUDE_CODE_OAUTH_TOKEN` | l'action documente ce jeton pour les abonnés Pro/Max ; l'usage est rattaché à l'**abonnement** et non à une clé API | sources secondaires seulement ; **la page officielle de l'action n'a pas pu être lue** (hors liste autorisée de cette session) |
| Comptage de l'usage « programmatique » (Actions, `claude -p`) sur l'abonnement | **sources contradictoires** : l'une annonce, à partir du 15/06/2026, un compte de crédits séparé au tarif API puis facturation à l'usage au-delà ; une autre indique que ce changement a été suspendu le 16/06 | **non résolu** → on ne peut pas garantir 0 € |
| Quotas/limites d'usage de l'abonnement | un dépassement peut bloquer les sessions interactives du réalisateur (coût indirect) | à mesurer |
| Conditions d'utilisation : jeton d'abonnement dans l'automatisation | usage « individuel » prévu ; automatisation par l'utilisateur lui-même tolérée selon les sources, pas de confirmation officielle lue | **non résolu** |

**Conclusion : avec le seul niveau de preuve disponible, l'absence de frais additionnels n'est PAS démontrée pour l'option OAuth → pas d'activation.**

## 4. Protocole avant toute activation (jamais sans le réalisateur)
1. Lire la documentation officielle actuelle de `anthropics/claude-code-action` et la page « facturation / limites d'usage » de l'abonnement ; confirmer **par écrit** que l'usage via `CLAUDE_CODE_OAUTH_TOKEN` n'entraîne ni crédits payants ni dépassement facturé, et que le **« paiement au-delà des limites » est désactivé** sur le compte.
2. Si ce point ne peut pas être établi : **renoncer** (décision déjà prise par le réalisateur) ; poursuivre les revues manuelles.
3. Sinon, test manuel unique : activer le modèle sur une PR **synthétique** (sans donnée client), relever l'usage affiché dans le compte avant/après, vérifier l'absence de frais, puis seulement décider.
4. Revue humaine du workflow par le réalisateur ; PR d'activation distincte de celle-ci.

## 5. Garde-fous prévus dans le modèle
Déclencheur manuel seul (`workflow_dispatch`, pas de `issue_comment`/`pull_request`/`schedule`) · acteur = propriétaire, bots refusés · `permissions: {}` par défaut puis minimum par job · jamais `pull_request_target`, jamais de fork · concurrence 1, `timeout-minutes: 15` · `--max-turns` borné · idempotence par SHA de commit (marqueur de commentaire) et plafond de tours par PR · consigne traitée comme **donnée**, jamais exécutée · aucun secret dans les logs ni les commentaires · runner standard uniquement.

## 6. Arrêt d'urgence (si un jour activé)
1. Supprimer le secret `CLAUDE_CODE_OAUTH_TOKEN` (Settings → Secrets and variables → Actions) et révoquer le jeton côté compte Claude.
2. Settings → Actions → General → « Disable actions » (ou désactiver le workflow seul).
3. Supprimer le fichier du workflow par une PR ; annuler les runs en cours (`gh run cancel`).

## 7. Critères d'acceptation de l'issue #30 — état
| # | Critère | État |
|---|---|---|
| 1 | PR de configuration, documentation, permissions exactes | ✅ (modèle + doc, inactifs) |
| 2 | Preuve d'un cycle revue → commentaire → correction sur PR synthétique | ⛔ **non fait, volontairement** : exige une exécution ; interdite tant que §4 n'est pas validé |
| 3 | Détection de boucles, idempotence par SHA, plafond, arrêt d'urgence | 🟡 spécifiés (§5–6), non éprouvés |
| 4 | Accord explicite du réalisateur avant secrets et activation | en attente |
| 5 | Tableau des coûts et contrôles de dépenses | ✅ §3 (avec points non résolus) |

Côté OpenAI : aucun chemin sans clé API facturée n'a été identifié ; l'option est abandonnée.
