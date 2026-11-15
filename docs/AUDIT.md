# Audit du socle (commit `0d8bbe2`) et corrections de la PR 1

Méthode : chaque bug ci-dessous est **prouvé** par un test qui échoue sur `0d8bbe2` et passe après correction. Les risques non prouvés sont listés séparément, avec la façon de les tester.

## Bugs prouvés (corrigés dans la PR 1)

| # | Bug | Preuve (test) | Correction |
| --- | --- | --- | --- |
| 1 | `scan --subdir X` **écrasait toute la bibliothèque** avec le seul sous-dossier | `test_scan_subdir_keeps_rest_of_library` | `refresh` incrémental : seuls les fichiers du périmètre sont touchés |
| 2 | Un nouveau `scan` **perdait** durée et présence d'audio déjà lues | `test_rescan_keeps_probe_metadata` | fiche conservée tant que taille + date sont identiques |
| 3 | `proxies` lisait une bibliothèque **périmée** : un fichier ajouté après le scan n'avait jamais de proxy | `test_proxies_do_not_rely_on_stale_library` | `proxies` rafraîchit la bibliothèque à chaque lancement |
| 4 | Changer `--height` ne refaisait **pas** les proxys (« déjà à jour ») | `test_proxy_rebuilt_when_height_changes` | fiche `<proxy>.json` : empreinte source + hauteur |
| 5 | Un fichier **remplacé par un plus ancien** (copie Drive qui restaure la date) gardait un proxy faux | `test_proxy_rebuilt_when_source_replaced_with_older_mtime` | comparaison taille + date exactes (pas « plus récent que ») |
| 6 | Le rendu **échouait** si un chemin contenait une apostrophe (`o'brien`, `Mariage d'Elie`) | `test_render_survives_apostrophes_in_paths` | échappement du fichier de concaténation |
| 7 | *(trouvé pendant la PR)* AAC encodé **par segment** : trou d'une image et possible clic à chaque raccord, cadence réelle 24,3 i/s au lieu de 25 | `test_joins_are_frame_exact_with_constant_frame_rate` | segments en PCM avec nombre d'images exact, AAC encodé une seule fois à la fin |
| 8 | *(trouvé pendant la PR)* Source à cadence variable : le plan perdait des images au début | `test_vfr_source_still_yields_exact_frame_count` | `fps=…:start_time=0` + image clonée en fin de source |

## Risques corrigés par conception (lecture du code, non prouvés par test avant)

- Le rendu écrivait directement sous le nom final : un arrêt laissait un fichier tronqué et détruisait le rendu précédent → écriture en `.part` puis renommage atomique ; durée vérifiée avant publication (`test_cancel_keeps_previous_render…`, `test_failed_music_pass_publishes_nothing`).
- Aucune reprise ni annulation : tout était à refaire → cache de segments (clé = source, plage, fondus, gain, cadence, résolution, encodeur), `CancelToken`, Ctrl+C propre.
- Un fichier illisible faisait échouer toute la création des proxys → échecs collectés, les autres continuent.
- `library.json` corrompu → mis de côté (`.corrupt-<date>`), jamais écrasé.
- Écritures JSON non atomiques → `os.replace`.
- Aucune validation avant rendu → `preflight` / `kolnoa check` (sources, plages, VFR, orientation, cadence, audio absent, musique, espace disque, ffmpeg/NVENC, chemins longs).

## Risques ouverts à tester sur le Shadow PC avec de vrais rushes

1. **Google Drive en streaming** : `ffprobe`/`ffmpeg` sur des fichiers de 15 à 25 Go situés sur `G:\` peuvent déclencher un téléchargement complet. À mesurer ; sinon copier le mariage en local avant analyse.
2. **`.kolnoa/` dans le dossier du mariage est synchronisé par Drive.** Proxys, cache et rendus peuvent peser des dizaines de Go. Recommandation : définir `KOLNOA_DATA_DIR` (ex. `D:\Kolnoa`) — implémenté, non imposé pour garder le comportement existant.
3. **NVENC non testé** : le bac à sable n'a pas de GPU ; seul le chemin x264 est couvert par les tests.
4. **Heure des fichiers Sony** : déduite de la date de modification (fuseau du PC) alors que DJI/Canon donnent l'heure de l'horloge de la caméra. Les décalages entre appareils sont à calibrer (PR B).
5. **Rushes log (S-Log3)** et 10 bits : conversion simple vers yuv420p, sans gestion colorimétrique (prévu PR D). Un rendu depuis du S-Log3 sera plat.
6. **Caractère `⁄` (U+2044) dans le nom d'un dossier Canon** observé dans le listing du Drive : Python/ffmpeg l'acceptent a priori, à confirmer sur Windows.
7. **Précision de l'import Drive** : la date de modification peut être celle de la synchro ; la taille reste fiable, d'où `--hash` (3 échantillons de 1 Mio) en secours.

## Ce qui n'est pas encore fait (issues #1 et #2)

Synchro audio/visuelle, pistes audio multiples, analyse des plans, timeline native, UI, effets/LUTs/transitions, assistant IA : voir `docs/ARCHITECTURE_V2.md`.
