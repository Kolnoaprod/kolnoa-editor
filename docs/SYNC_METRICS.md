# Métriques de synchronisation (signaux synthétiques)

Généré par `python scripts/bench_sync.py` — Python 3.13.18, numpy 2.5.3, durée du bench
270 s. Graines fixes : erreurs et taux reproductibles ; les temps dépendent de la machine.

**Ce que ces chiffres ne prouvent pas** : ils décrivent des signaux synthétiques (parole simulée, scènes de luminosité
simulées). Les seuils par défaut (`SyncConfig`) sont des valeurs initiales ; la calibration terrain sur de vrais rushes
reste à faire (voir `docs/SYNC.md`, section « Rushes nécessaires »).
Objectif d'acceptation : erreur de décalage ≤ 40 ms (une image à 25 i/s), aucun faux « ok ».

### Décalages positifs et négatifs (SNR 25 dB)

| tirages | états | erreur médiane (ms) | p95 (ms) | max (ms) | faux « ok » | temps médian (s) |
| --- | --- | --- | --- | --- | --- | --- |
| 24 | {'ok': 24} | 0.01 | 0.06 | 0.07 | 0 | 0.57 |

### Bruit et micro différent (passe-bas + écho, gain 0,4)

| SNR | tirages | états | erreur médiane (ms) | p95 (ms) | faux « ok » |
| --- | --- | --- | --- | --- | --- |
| 20 dB | 12 | {'ok': 12} | 0.20 | 0.23 | 0 |
| 10 dB | 12 | {'ok': 12} | 0.17 | 0.28 | 0 |
| 0 dB | 12 | {'ok': 12} | 0.15 | 0.17 | 0 |
| -5 dB | 12 | {'ok': 12} | 0.14 | 0.23 | 0 |
| -10 dB | 12 | {'ok': 12} | 0.08 | 0.22 | 0 |
| -15 dB | 12 | {'impossible': 12} | - | - | 0 |

### Silences dans l'angle

| cas | tirages | états | erreur médiane (ms) | p95 (ms) |
| --- | --- | --- | --- | --- |
| 1 silence de 45 s | 12 | {'ok': 12} | 0.01 | 0.02 |
| 2 silences (45 s + 20 s) | 12 | {'ok': 12} | 0.01 | 0.03 |
| silence 70 % du clip | 12 | {'ok': 12} | 0.01 | 0.03 |

### Dérive d'horloge (clip de 4 min, SNR 20 dB)

| dérive vraie | états | erreur médiane (ppm) | erreur max (ppm) | erreur max du décalage (ms) |
| --- | --- | --- | --- | --- |
| -3000 ppm | {'ok': 6} | 0.71 | 6.05 | 0.97 |
| -1000 ppm | {'ok': 6} | 0.28 | 1.36 | 0.17 |
| -300 ppm | {'ok': 6} | 0.07 | 0.26 | 0.04 |
| -50 ppm | {'ok': 6} | 0.70 | 1.56 | 0.22 |
| +50 ppm | {'ok': 6} | 0.57 | 0.99 | 0.27 |
| +300 ppm | {'ok': 6} | 0.18 | 0.43 | 0.06 |
| +1000 ppm | {'ok': 6} | 0.07 | 1.09 | 0.18 |
| +3000 ppm | {'ok': 6} | 0.68 | 6.02 | 0.94 |

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
| 60 s | 30 min | ok | 2.73 | 0.57 |
| 300 s | 30 min | ok | 0.03 | 3.39 |
| 600 s | 30 min | ok | 0.06 | 1.75 |

### Synchro visuelle (drone muet) — EXPÉRIMENTAL, 4 événements lumineux communs

| scénario | tirages | répartition (état, vérité) | erreur médiane du 1er candidat (s) |
| --- | --- | --- | --- |
| événements communs | 100 | {'ambigu/faux': 1, 'ambigu/juste': 3, 'faible/faux': 8, 'faible/juste': 63, 'impossible/faux': 12, 'proposition/juste': 13} | 0.050 |
| scènes sans rapport | 500 | {'faible': 70, 'impossible': 425, 'ambigu': 2, 'proposition': 3} | - |
