# Ancres de contenu — tests et performances (A1-3)

## Validation
- **Oracle indépendant** (`tests/anchors_oracle.py`) : balayage exhaustif image par image en fractions exactes ; n'importe rien du module d'ancres.
- **10 000 scénarios reproductibles** (graine = numéro de scénario ; `KOLNOA_ANCHOR_SCENARIOS` pour changer le nombre) : 24/25/30/50/60 i/s, 1–3 médias, 1–3 pistes, 6–10 opérations réelles du moteur (rasoir, déplacement, rognage, suppression, suppression avec ripple), comparaison **après chaque opération** puis après **annulation complète** par la pile des inverses.
- **1 500 scénarios multicam** (axe du groupe, décalages et dérives ±200 ppm, changements d'angle, rasoir, rognage).
- **Refus explicites** : VFR, cadence non entière (23,976/29,97), vitesse ≠ 1 — 1 200 cas de création refusée + 300 cas de passage ultérieur à `non_supportée`.
- Limites : les `src_in` des scénarios sont alignés sur la grille d'images (sinon des chevauchements de quelques µs sont indiscernables d'un arrondi) ; pas de liens image+son (`linked=False`) ; la dérive est supposée linéaire (pas de points manuels multiples).

## Mesures (`python tools/bench/bench_anchors.py`)
Synthétique (8 pistes, médias fictifs). Froid = construction de l'index + 1re résolution ; chaud = une ancre sur index construit (5 passes × toutes les ancres).

| plans | ancres | index froid (ms) | 1re résolution froide (ms) | chaud p50 (ms) | chaud p95 (ms) | chaud p99 (ms) | lot complet (ms) |
|---|---|---|---|---|---|---|---|
| 200 | 20 | 1.35 | 0.022 | 0.0091 | 0.0186 | 0.054 | 1.5 |
| 2000 | 200 | 13.1 | 0.04 | 0.0086 | 0.015 | 0.0295 | 15.4 |
| 10000 | 1000 | 90.79 | 0.063 | 0.0092 | 0.0145 | 0.0267 | 103.0 |
| 50000 | 5000 | 1005.57 | 0.09 | 0.0142 | 0.0252 | 0.0477 | 1192.1 |

Machine : Linux-6.18.44-fc-v553-x86_64-with-glibc2.39, 2 cœurs, Python 3.13.18.

Lecture honnête :
- Résolution **à chaud** : p99 ≤ 0,05 ms jusqu'à 50 000 plans (le seuil visé de 5 ms est tenu avec une grande marge sur cette machine à 2 cœurs).
- **À froid** : la construction de l'index coûte ≈ 1 s pour 50 000 plans (≈ 20 µs/plan) ; elle est payée une fois par état de timeline, pas par ancre. Au-delà de quelques milliers de plans, il faudra la construire hors du fil d'interface.
- Le résolveur reconstruit son index à chaque nouvelle timeline (pas d'index incrémental) ; `Resolver.is_fresh()` / `check_fresh` détectent un index périmé.
- Mesures prises dans un conteneur partagé : à refaire sur la machine de montage du réalisateur avant toute décision de seuil.
