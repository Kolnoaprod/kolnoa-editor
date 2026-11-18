# « Amélioration du plan » — découpage des PR et risques (ralentis, stabilisation, prolongation)

Consignes du réalisateur (stabilisation **Standard = intensité 45 %**, prolongation **2 s**, prévisualisation, rendu non destructif) et de GPT (#39, 18/11). Ordre : terminer #39 → ralentis → stabilisation → étude/prototype de génération. Chaque lot = PR brouillon séparée, avec CI et export relu. Aucune fusion sans validation.

## Principe commun (non destructif)
Un effet est une **donnée du plan** (`Clip.extra`), jamais un fichier modifié : `speed` (existant), `fx.stabilize {preset, intensite, lissage, recadrage}`, `fx.extend {secondes, methode, media_derive}`. Mêmes `Session`/transactions : annuler, provenance (une retouche manuelle reste protégée), enregistrer/rouvrir. Les caches (analyse de mouvement, médias dérivés) sont **recalculables** et indexés par (rush, plage, paramètres) ; le rush original n'est jamais écrit. Le rendu applique réellement l'effet ; si un effet n'est pas rendu, l'export le **refuse ou l'avertit** (jamais d'effet silencieusement ignoré).

## Découpage
| PR | Contenu | Dépend de |
|---|---|---|
| **R1** ralentis au rendu | sémantique de `speed` (durée source = durée timeline × vitesse), `flatten` + rendu vidéo (sélection d'images ; 50 i/s source → ralenti ×0,5 fluide sur timeline 25 i/s sans interpolation), audio (coupé / conservé avec hauteur préservée), contrôles (src_in, bornes, `check_flatten`), avertissement si images dupliquées | #39 |
| **R2** vitesse : interface + prompt | opération `set_speed` sur plan/sélection/zone I-O avec **découpe** (ralentis partiels), vitesses 0,5 / 0,75 / 1 / 2, panneau « Amélioration du plan », consignes « ralentis à 50 % » ; mode interpolation optique **distinct**, avertissé | R1 |
| **S1** stabilisation moteur | vérifier `vidstab` dans le FFmpeg embarqué (présent ici ; **à vérifier sur la build Windows**), détection du mouvement 1 passe en cache par (rush, plage), transformation au rendu ; préréglages Léger / **Standard 45 %** / Fort, lissage, recadrage-zoom | R1 (rendu par tronçon) |
| **S2** stabilisation interface | activer/désactiver sur plan/sélection/zone, prompt « stabilise ces plans mais conserve les mouvements volontaires », aperçu avant/après, **détection de plans instables = proposition seulement** | S1, R2 |
| **G0** prolonger avec de vraies images | d'abord chercher la matière disponible dans le rush (poignées), sinon ralenti de la fin ; 2 s par défaut | R1 |
| **G1** prototype de génération (isolé, hors application) | backend local interchangeable (p. ex. ComfyUI + modèle de continuation), **mesure VRAM / durée / qualité / cohérence / visages**, gestion des échecs ; aucune promesse 4K50 | — |
| **G2** intégration générative | média dérivé + provenance (source, modèle, paramètres, durée, date), aperçu **avant acceptation**, insertion annulable/supprimable/régénérable, avertissement explicite (détails inventés, personnes) ; jamais déclenchée silencieusement | G0, G1 |
Backlog conservé : regroupement manuel des caméras sans horodatage fiable, ducking audio.

## Risques techniques (à lever avant de promettre)
1. **Ralentis** : timeline 50 i/s + rush 50 i/s à ×0,5 = chaque image deux fois (saccadé) → avertissement et mode interpolation séparé. Audio en ralenti : hauteur préservée mais artefacts possibles à 0,5× ; défaut = audio coupé sur ralenti.
2. **Multicam + vitesse** : le son maître et la synchro ne suivent pas une vitesse ≠ 1 (le plan multicam ralenti = son coupé tant que non traité) ; à ne pas masquer.
3. **Stabilisation** : le recadrage fait perdre du champ (zoom auto) ; les panoramiques volontaires peuvent être « corrigés » à forte intensité → Standard 45 % par défaut, lissage borné ; le détecteur d'instabilité ne propose, n'applique jamais. `vidstab` dépend du FFmpeg embarqué (build Windows à vérifier). Coût 4K50 non mesuré (station).
4. **Prolongation** : sans matière dans le rush, seul le ralenti de la fin est « réel » ; la génération (G1/G2) dépend de la VRAM (RTX A4500 : 20 Go annoncés, non mesurés ici), de modèles gratuits compatibles, de la cohérence temporelle et des visages — **peut s'avérer inutilisable** ; l'étudier ne la promet pas. Budget 0 € : uniquement un backend local.
5. **Rendu par tronçon** : un plan coupé en plusieurs tronçons (recouvert par V2) est stabilisé tronçon par tronçon (raccords de lissage possibles) ; à documenter/mesurer.
