# Choix d'angle par qualité visuelle (remplace la rotation équitable)

Branche `feat/angle-scoring`, empilée sur #38. Brouillon.

## Ce qui est CODÉ ET TESTÉ (vraies vidéos ffmpeg)
- **Analyse locale légère** (`auto/visual.py`) : 1 image/s en 160×90 gris, une passe ffmpeg par angle, **proxy à jour utilisé s'il existe**, cache disque, désactivable (`VisualParams(enabled=False)`). Mesures : netteté relative (variance du laplacien), exposition (luminance, ombres bouchées, hautes lumières). Visages : optionnel (OpenCV si installé, désactivé par défaut).
- **Sélection** (`auto/angles.py`) : seuls les angles **synchronisés avec confiance ET couvrant tout le plan** sont candidats. Qualité 0–1 par seconde (une image inexploitable — noire/brûlée — annule sa « netteté », qui n'est alors que du bruit) ; programmation dynamique avec **coût de changement** (moindre dans une pause de la parole), **durée minimale** (plans courts absorbés), bonus au **maître** à égalité. **Aucune coupe imposée par la durée** : un plan dure tant que rien de meilleur n'apparaît.
- **Repli déterministe** sans analyse exploitable ou critères éteints : angle maître, un seul plan continu, motif « analyse indisponible ».
- **Explicable et corrigeable** : chaque plan garde `extra.angle_choice` (motif, scores des angles) ; la fenêtre affiche à la sélection l'angle, le motif, les scores, les alternatives fiables et les angles écartés ; le changement manuel (sélection/zone) reste protégé par la provenance et annulable.
- **Tests** : A flou/B net/C sombre (C jamais retenu, B choisi dès qu'il couvre, ≤ 3 plans) ; angles équivalents (aucune coupe) ; bruit de mesure (pas de va-et-vient) ; plans courts absorbés ; angle non synchronisé jamais retenu ; critères éteints → maître ; cache (2ᵉ passe 100 % cache) ; automatique → angle imposé à la main → consigne → enregistrer → rouvrir → export identique, synchro intacte.

## Heuristique PROVISOIRE (à calibrer sur tes rushes)
Poids netteté 0,55 / exposition 0,45 ; coût de changement 0,35 (0,15 en pause) ; durée minimale 3 s ; bonus maître 0,04 ; saturation de la netteté à 60 % du meilleur angle. Aucune de ces valeurs n'est une vérité artistique.

## NON mesuré / NON fait (dit franchement)
- **Stabilité** : non mesurée (1 image/s ne distingue pas bougé et changement de plan). **Contenu / sujet / visages / expressions** : non évalués ; pas de reconnaissance d'identité.
- La netteté est **relative entre angles au même instant** : un angle volontairement plus large peut paraître « moins net ». C'est pourquoi le maître est favorisé en cas de doute.
- **Coût** : 60 s de 4K50 H.264 analysées en ~30 s (CPU du bac à sable, un seul angle, mesure bruitée) soit ≈ 0,5× temps réel ; mémoire ≈ 40 Mo. **Valeur indicative uniquement** : à mesurer sur la station Windows (proxy, décodage matériel). Cache et interrupteur prévus.
- Synchro et qualité visuelle validées sur vidéos/sons **synthétiques** ; aucun rush réel n'a été vu.
- **Ducking audio** : non fait (prévu après les fondus/transitions, ou en parallèle sur branche indépendante).
- **Regroupement manuel** et recherche audio plus large pour les caméras à horodatage absent/erroné : non fait (la détection par horodatage reste l'unique façon de proposer un groupe) — à prévoir dans l'interface.
