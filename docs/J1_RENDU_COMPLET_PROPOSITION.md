# J1 — Rendu MP4 de la timeline complète : proposition technique (SANS implémentation)

Statut : **proposition à valider** (jalon J1 de `docs/BETA_AVANCEMENT.md`). Aucun code produit n'est écrit tant que le réalisateur n'a pas dit oui.
Lecture du code existant : `renderer.py` (PR 1), `timeline/convert.py` (PR A), `timeline/audio_mix.py` (PR #16).

## 1. Constat
- `timeline-render` passe par `to_decision_list`, qui **refuse** : plusieurs pistes vidéo, plans multicam, trous, canaux explicites, gains de plan… (erreurs ou « non exportés »).
- Le moteur de rendu actuel sait déjà : encoder chaque segment (cache par empreinte, reprise après interruption, écriture atomique, raccords à l'image), concaténer, vérifier la durée.
- `render_mix` produit déjà le **mixage audio complet** de la timeline (pistes, source maître multicam, dual mono, gains, fondus, sonie/crête) en WAV, mais ne sait pas le coller à l'image.
- `Timeline.music` (champ historique) n'est **pas** mixé par `render_mix` : la musique doit être un plan sur une piste audio (rôle « musique »).

## 2. Idée directrice : aplatir, puis réutiliser
**Le multicam n'a pas besoin d'un rendu à part** : un plan multicam est un plan comme un autre (`media` = fichier de l'angle montré, `src_in` en temps de l'angle). Un plan de coupe est un plan sur une piste vidéo plus haute. Donc :

1. **Aplatissement vidéo** (`flatten_video(tl) -> list[VideoSpan]`) : pour chaque image de la timeline, le plan visible est celui de la piste vidéo la plus haute non muette ; on obtient des segments [début, fin) contigus, chacun = (plan, décalage source). Zones sans plan = **noir** explicite (avertissement du contrôle préalable, jamais un trou silencieux). Fonction pure, testable à l'image près.
2. **Encodage des segments** par le code existant (`_encode_segment`, cache, reprise, atomicité) — généralisé pour recevoir un `VideoSpan` au lieu d'un `Segment` (même empreinte de cache + piste/ordre de superposition).
3. **Audio** : `render_mix` sur la timeline entière → WAV ; la vidéo concaténée est **copiée** (`-c:v copy`) et le WAV encodé en AAC dans le MP4 (une seule passe de mux).
4. **Contrôles** : durée vidéo = durée timeline (à l'image) ; durée audio = durée vidéo (±1 échantillon de tolérance documentée) ; aucune publication si l'un échoue (comportement actuel conservé).

## 3. Périmètre J1 / hors J1
| Dans J1 | Hors J1 (jalons ultérieurs) |
|---|---|
| Superposition de pistes vidéo en **opaque** (plan de coupe qui remplace l'image) | Transitions vidéo, fondus enchaînés d'image, incrustations semi-transparentes (J3) |
| Multicam rendu via les plans (changements d'angle, source maître de `render_mix`) | Étalonnage/LUT dans le rendu (PR #11 + J3) |
| Musique et ambiances = plans sur pistes audio, mixage + sonie cible | Ducking automatique, musique pilotée par l'assistant |
| Trous → noir + avertissement ; proxys et rendu final | Ralentis/vitesses ≠ 1 (non supportés par la timeline) |
| Audio avec cadences mixtes (rééchantillonnage déjà géré) | Fréquences d'images mixtes non entières (refus clair) |

## 4. Découpage proposé (PR brouillon, indépendantes, chacune avec ses tests)
| PR | Contenu | Critères de validation |
|---|---|---|
| J1-a | `flatten_video` + contrôle préalable (trous, pistes mutées, plan hors média) — **sans rendu** | oracle par balayage image par image ; propriétés sur scénarios aléatoires (comme A1-3) ; cas : coupe, plan de coupe, trou, piste mutée |
| J1-b | Rendu des `VideoSpan` (cache, reprise, atomicité) avec sources ffmpeg synthétiques | nombre d'images exact ; raccords à l'image (pas de dérive sur 1 000 coupes) ; reprise après annulation ; invalidation de cache à la modification d'un plan |
| J1-c | Mux audio (`render_mix`) + vidéo ; `timeline-render` complet ; vérification durée A/V | durée A/V identique ; décalage A/V mesuré par impulsion synthétique < 1 échantillon à l'export ; sonie/crête rapportées |
| J1-d | Parcours bout en bout : timeline multicam + plan de coupe + musique → MP4, test de non-régression « film de référence A » synthétique | MP4 relu par ffprobe (durée, fps, pistes) ; aucune modification des rushes (octets+mtime) |

## 5. Risques et choix à valider
1. **Précision A/V** : le WAV mixé est flottant, la vidéo est en images ; l'alignement se fait à `start/fps` — à prouver par impulsion de test (J1-c), pas à supposer.
2. **Coût** : ré-encodage par segment (cache) ; un montage de 40 min en 4K50 sera long sans GPU — mesure à faire avec J2 sur la machine du réalisateur.
3. **Proxys vs final** : le rendu « proxy » reste la voie de contrôle rapide ; le « final » lit les originaux (lecture seule).
4. **Ancres (A1)** : le rendu n'en dépend pas ; les directives de l'assistant (A2) cibleront ensuite les plans par `ConfirmedTarget`.
5. **Ordre** : J1-a n'a aucune dépendance ffmpeg et peut démarrer dès validation ; J2 (mesures GPU) peut avancer en parallèle car il ne touche pas au code produit.

## 6. Ce que je demande
Validation de **J1-a à J1-d** (ou d'une partie), dans cet ordre, en brouillon, sans fusion. Sans validation, je n'écris aucun code de rendu.
