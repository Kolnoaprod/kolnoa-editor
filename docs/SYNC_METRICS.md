# Métriques de synchronisation (signaux synthétiques)

Généré par `python scripts/bench_sync.py` — Python 3.13.18, numpy 2.5.3, durée du bench
277 s. Graines fixes : erreurs et taux reproductibles ; les temps dépendent de la machine.

**Ce que ces chiffres ne prouvent pas** : ils décrivent des signaux synthétiques (parole simulée, scènes de luminosité
simulées). Les seuils par défaut (`SyncConfig`) sont des valeurs initiales ; la calibration terrain sur de vrais rushes
reste à faire (voir `docs/SYNC.md`, section « Rushes nécessaires »).
Objectif d'acceptation : erreur de décalage ≤ 40 ms (une image à 25 i/s), aucun faux « ok ».

### Décalages positifs et négatifs (SNR 25 dB)

| tirages | états | erreur médiane (ms) | p95 (ms) | max (ms) | faux « ok » | temps médian (s) |
| --- | --- | --- | --- | --- | --- | --- |
| 24 | {'ok': 24} | 0.01 | 0.05 | 0.06 | 0 | 0.62 |

### Bruit et micro différent (passe-bas + écho, gain 0,4)

| SNR | tirages | états | erreur médiane (ms) | p95 (ms) | faux « ok » |
| --- | --- | --- | --- | --- | --- |
| 20 dB | 12 | {'ok': 12} | 0.18 | 0.19 | 0 |
| 10 dB | 12 | {'ok': 12} | 0.17 | 0.29 | 0 |
| 0 dB | 12 | {'ok': 12} | 0.15 | 0.15 | 0 |
| -5 dB | 12 | {'ok': 12} | 0.13 | 0.22 | 0 |
| -10 dB | 12 | {'ok': 12} | 0.09 | 0.21 | 0 |
| -15 dB | 12 | {'impossible': 12} | - | - | 0 |

### Silences dans l'angle

| cas | tirages | états | erreur médiane (ms) | p95 (ms) |
| --- | --- | --- | --- | --- |
| 1 silence de 45 s | 12 | {'ok': 12} | 0.02 | 0.02 |
| 2 silences (45 s + 20 s) | 12 | {'ok': 12} | 0.01 | 0.02 |
| silence 70 % du clip | 12 | {'ok': 12} | 0.01 | 0.02 |

### Dérive d'horloge (clip de 4 min, SNR 20 dB)

| dérive vraie | états | erreur médiane (ppm) | erreur max (ppm) | erreur max du décalage (ms) |
| --- | --- | --- | --- | --- |
| -3000 ppm | {'ok': 6} | 0.97 | 5.72 | 0.99 |
| -1000 ppm | {'ok': 6} | 0.41 | 1.67 | 0.18 |
| -300 ppm | {'ok': 6} | 0.03 | 0.51 | 0.07 |
| -50 ppm | {'ok': 6} | 0.64 | 1.57 | 0.23 |
| +50 ppm | {'ok': 6} | 0.52 | 1.07 | 0.28 |
| +300 ppm | {'ok': 6} | 0.07 | 0.46 | 0.06 |
| +1000 ppm | {'ok': 6} | 0.06 | 0.59 | 0.10 |
| +3000 ppm | {'ok': 6} | 2.37 | 6.14 | 0.94 |

### Audio sans aucun rapport avec le maître

| tirages | états | faux « ok » | temps médian (s) |
| --- | --- | --- | --- |
| 100 | {'impossible': 100} | 0 | 0.04 |

### Clip avec un saut interne (audio recollé)

| tirages | détectés « discontinu » à 2 segments |
| --- | --- |
| 6 | 6 |

### Temps de calcul (tableaux en mémoire ; la lecture de vrais fichiers par ffmpeg s'ajoute)

| clip | maître | état | erreur (ms) | temps (s) |
| --- | --- | --- | --- | --- |
| 60 s | 30 min | ok | 2.74 | 0.62 |
| 300 s | 30 min | ok | 0.05 | 3.36 |
| 600 s | 30 min | ok | 0.03 | 1.74 |

### Synchro visuelle (drone muet) — EXPÉRIMENTAL, 4 événements lumineux communs

| scénario | tirages | répartition (état, vérité) | erreur médiane du 1er candidat (s) |
| --- | --- | --- | --- |
| événements communs | 100 | {'faible/faux': 2, 'faible/juste': 69, 'impossible/faux': 19, 'impossible/juste': 8, 'proposition/juste': 2} | 0.055 |
| scènes sans rapport | 500 | {'impossible': 496, 'faible': 4} | - |
