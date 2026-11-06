# Decision-1 : de la mesure au juge Foundry — notes orateur

**Support de présentation technique.**

[Présentation autonome](decision-models-foundry-evaluator-fr.html). 9 séquences en 3 actes, exactement 420 secondes. Les animations démarrent seules à l’entrée de chaque slide ; les trois vidéos officielles s’ouvrent au clic dans un cadre pointant vers commandline.microsoft.com ; aucun autre appel réseau.

## Commandes

← / → : slides ; O : sommaire ; N : notes ; L : lecture ; T : thème ; F : plein écran ; Espace : chrono ; R : rejouer l’animation ; P : pause ; 1 / 2 : vitesse ×1 / ×2 sur le mécanisme. Slide 4 : statique, les onglets choice / noul / score se choisissent à la main. Les tuiles de la slide 8 se parcourent aux flèches et suspendent le cycle au survol ou au focus. Vidéos : lecture manuelle, son coupé au départ.

## Conducteur

| # | Acte | Station | Slide | Budget |
|---:|---|---|---|---:|
| 1 | Le modèle | Contrat | Contrat du modèle | 25 s |
| 2 | Le modèle | Mécanisme | Écrire un token à la fois, ou lire une distribution | 65 s |
| 3 | Le modèle | Origine | Base et post-entraînement | 35 s |
| 4 | Le modèle | API | API : choice / noul / score | 55 s |
| 5 | Le juge | Juge | Un juge, c’est une mesure | 25 s |
| 6 | Le juge | Foundry | Foundry : le juge devient un évaluateur endpoint | 50 s |
| 7 | Le juge | Résultats | Notre mesure : mêmes 360 jugements par juge | 55 s |
| 8 | Les usages | Ailleurs | Le même motif, ailleurs | 55 s |
| 9 | Les usages | Limites | Ce que ça fait bien, mal, et avec qui | 55 s |
| | | | **Total** | **420 s** |

## 1 — Contrat du modèle

**00:00 → 00:25 · 25 s · l’animation tourne seule (~6 s)**

Microsoft-Decision-1 reçoit un état et des questions typées, et renvoie des choix ou des valeurs numériques : pas d’explication libre. Les états de démonstration sont synthétiques ; les réponses viennent réellement de notre déploiement. Fil rouge de la présentation : le modèle mesure, le code décide, le LLM écrit. Voyons d’abord comment il produit sa réponse.

**Précisions et sources.** Les états envoyés au modèle sont synthétiques et non confidentiels. Les réponses affichées sont des captures réelles (14 appels HTTP 200, 36 jugements) ; ni l’ouverture du support ni sa lecture ne lancent d’inférence. Les trois verbes annoncent le fil rouge : mesurer (modèle de décision), décider (code applicatif), écrire (LLM).

## 2 — Écrire un token à la fois, ou lire une distribution

**00:25 → 01:30 · 65 s · l’animation tourne seule (~22 s) ; R rejoue, P met en pause, 2 passe en ×2**

Même ticket des deux côtés : « The API returns 500 on every call ». À gauche, un LLM génératif. Le Transformer lit tout le ticket : c’est le préremplissage, qui remplit le cache de clés et valeurs. Il calcule ensuite un score par token du vocabulaire, de l’ordre de cent cinquante mille ; le softmax les transforme en probabilités ; on choisit un token, on l’ajoute, et on recommence : dix étapes pour ce petit JSON. À droite, le modèle de décision calcule une probabilité par option en une seule lecture et renvoie une réponse typée. Ces valeurs viennent d’un vrai appel sur notre déploiement.

Le softmax n’est pas la différence : les deux côtés normalisent. Ce qui change, c’est l’espace de sortie, des labels fermés, et l’absence de boucle de décodage. Le bouton « LLM contraint » montre le cas le plus favorable au LLM : une grammaire lui impose de ne répondre que par un label autorisé, donc une seule étape. D’où vient ce modèle ? Slide suivante.

**Précisions et sources.** Public : l’article Command Line du 10/10/2026 décrit un Qwen3.5-9B post-entraîné pour un scoring de décision en un passage, avec une probabilité calibrée par option ; la documentation Learn décrit une sortie typée, sans texte libre. Non documentés : encodage des labels, normalisation exacte (softmax sur tout le vocabulaire ou sur les labels), nombre de passes internes, partage du cache KV (annexe H). Le compteur « 1 lecture » désigne le contrat illustré, pas un profilage de passes avant. Les distributions de la voie gauche sont illustratives (logits inventés, softmax calculé à l’écran) ; ~150 000 est un ordre de grandeur de taille de vocabulaire, pas une propriété attribuée à Qwen3.5. La voie droite est la capture redthread-route du 06/11/2026. Sur nos 14 appels, usage.output_tokens égale le nombre de questions : simple observation, qui ne révèle pas l’implémentation. LLM contraint : décodage contraint par une grammaire (schéma JSON, liste de valeurs autorisées) qui masque tout token hors des sorties permises ; c’est toujours un LLM autorégressif, avec une boucle de décodage (ici une étape) et sans distribution typée en sortie. Origine et entraînement : slide 3.

## 3 — Base et post-entraînement

**01:30 → 02:05 · 35 s · l’animation tourne seule (~6 s)**

D’où vient-il ? D’un LLM classique : Qwen3.5-9B, pré-entraîné à prédire le token suivant. Ce qui change, c’est l’étape d’après. Le RLHF récompense la réponse qu’un humain préfère : on obtient un assistant de chat. Le RLVR récompense un résultat qu’un programme vérifie : on obtient un modèle de raisonnement, plus lent. Un modèle de décision est récompensé sur la justesse de ses probabilités. Chez Jev, cela s’appelle RLCD. Pour Decision-1, la recette n’est pas publiée.

**Précisions et sources.** Pré-entraînement : prédire le token suivant sur un grand corpus. Post-entraînement : ajuster ce modèle avec un signal de récompense. RLHF (Ouyang et al., arXiv 2203.02155) : modèle de récompense appris sur des préférences humaines, puis RL (PPO ou GRPO) ; effets documentés : complaisance, ton assuré, « mode dropping ». RLVR (DeepSeek-R1, arXiv 2501.12948 ; Tulu 3, arXiv 2411.15124) : la récompense vient d’un vérificateur automatique (tests, réponse connue) ; donne des modèles de raisonnement, plus lents à l’inférence. RLCD (« Reinforcement Learning for Calibrated Decisions », TypeSafe / Jev) : objectif déclaré = probabilités calibrées (sur un groupe de réponses données à 0,8, environ 80 % sont justes). D’après le guide de synthèse de Roitman (arXiv 2610.33775, source tierce), le signal est une règle de score propre (Brier, log-loss) sur des tâches de décision à résultat vérifiable, sans jamais générer de texte libre. TypeSafe n’a publié ni fonction de récompense, ni jeu de données, ni code, ni article (constat de l’annuaire systemonemodels.org) : nous retenons l’objectif et la famille de récompense, pas une recette. Les données de Jev seraient synthétiques (déclaration rapportée par TechCrunch le 18/09/2026). Homonyme sans rapport : RLCD de Yang et al. (arXiv 2307.12950) = Reinforcement Learning from Contrastive Distillation. Autres approches publiques pour entraîner des modèles de décision (annexe I) : fine-tuning supervisé avec perte de Brier ou entropie croisée, distillation depuis un LLM, apprentissage contrastif ; Cloudflare Clef (backbone Qwen gelé, adaptateurs de rang 256, entropie croisée à lissage + Brier, seconde étape appelée RLCD, auto-déclaré) ; System One Gemma (tête de décision, masque bidirectionnel, LoRA). Pour Decision-1 : publié = Qwen3.5-9B post-entraîné pour le scoring en un passage (article Command Line du 10/10/2026) ; observé chez nous : usage.output_tokens = nombre de questions. Le guide Roitman écrit « Qwen3.5-9B + RLCD post-training » dans un tableau, mais sa source (l’article Microsoft) dit seulement « post-entraîné » : hypothèse tierce, non confirmée. Aucune source n’indique RLHF, DPO ou un autre algorithme précis pour Decision-1. Non documenté : données, loss, algorithme, calibration, encodage des labels. Nous n’avons pas entraîné les poids.

## 4 — API : choice / noul / score

**02:05 → 03:00 · 55 s · slide statique : choisir choice, noul ou score à la main**

Reprenons le ticket de la slide 2 : « l’API renvoie 500, le checkout est en panne depuis neuf heures, j’ai déjà contacté le support deux fois ». Trois questions sur ce même texte, trois types de sortie. Choice : à quelle équipe ? On donne les options avec leur définition, on récupère une probabilité par option : engineering, quasiment un. Score : quelle gravité ? Quatre niveaux ordonnés, la sortie est la moyenne pondérée des niveaux : deux virgule quatre-vingt-quinze sur trois, donc critique. Noul : l’utilisateur a-t-il déjà contacté le support ? Une probabilité de vrai : zéro virgule neuf neuf neuf neuf. Pas de phrase, pas de justification : des nombres que le code peut seuiller. Un score ordonné sur une rubrique : c’est ce que fait un juge.

**Précisions et sources.** Capture redthread-route du 06/11/2026 (état synthétique, en anglais tel qu’envoyé, sortie réelle) : team = engineering, p = 0.9997822297957877 (confidence 0.9996733446936816) ; severity = 2.9457072992756843 = 0×0.00011677 + 1×0.00028012 + 2×0.05338213 + 3×0.94622097 ; repeat_contact (noul) = 0.9999251537827373. 157 tokens d’entrée, 3 de sortie (un par question), latence client 587 ms. choice : une option parmi N ; score : niveaux ordonnés ; noul : de 0 à 1. Le score n’est pas une probabilité d’exactitude. Le champ confidence du choix est distinct du maximum de sa distribution ; ne pas supposer sa formule. Aucune rationale n’est renvoyée. Le score affiché omet confidence et legend. Code copiable (curl, Python avec erreurs et délais) : annexe B ; requête et réponse complètes : bouton « Corps complet + réponse ».

## 5 — Un juge, c’est une mesure

**03:00 → 03:25 · 25 s · l’animation tourne seule (~8 s)**

Un juge, c’est une mesure. Un LLM-juge écrit environ trois cents tokens de justification, puis notre code en extrait un « 4 sur 5 ». Si le logiciel ne consomme que la note, pourquoi lui faire écrire ? Un modèle de décision rend directement la mesure, en probabilités typées. Le modèle mesure, le code décide, le LLM écrit.

**Précisions et sources.** Illustration : 4/5 et 0,82 sont deux contrats, pas deux jugements mesurés sur le même cas ; les durées de l’animation ne sont pas à l’échelle. Écrire une justification n’est pas une nécessité universelle : un LLM contraint peut répondre court (slide 2, bouton « LLM contraint »). La slide 4 a posé le pont : une rubrique de jugement est un score ordonné, exactement ce que renvoie une question de type score.

## 6 — Foundry : le juge devient un évaluateur endpoint

**03:25 → 04:15 · 50 s · suivre le paquet de la ligne de dataset jusqu’aux résultats**

Où brancher cette mesure ? Dans Foundry, comme évaluateur de type endpoint, sans changer le framework d’évaluation. Le run prend nos lignes de dataset et appelle une connexion authentifiée. Notre Azure Function reçoit ce contrat, schema_version 0.0.1, mappe les données vers l’état et la rubrique, appelle le endpoint /systemone de Foundry, qui interroge le modèle Decision-1 déployé dans Foundry, puis normalise la réponse typée en score, status et properties. Foundry range tout dans ses résultats natifs.

Deux extraits de notre dépôt : la définition d’évaluateur de type endpoint avec son connection_name, et le testing_criteria avec evaluator_name et data_mapping. Changer de juge, c’est changer evaluator_name. Nous avons trois évaluateurs Decision et trois évaluateurs LLM, avec exactement le même texte de rubrique. C’est une preview, avec un délai de trente secondes.

**Précisions et sources.** Source : src/foundry_decision_eval/foundry.py, README et contrat Learn, lus sans modification du dépôt. Le helper utilise project.evaluators avec l’en-tête Foundry-Features: Evaluations=V1Preview. Contrat 0.0.1, data.item / data.sample ; réponse score / status / properties, reason null par défaut. Compatibilité de la preview déployée : status « Completed » et reason vide, sans rationale fabriquée ; les erreurs ne deviennent jamais un score. Foundry documente 30 s ; l’adaptateur borne l’inférence à 25 s. Changer evaluator_name suppose une définition et un data_mapping compatibles (un évaluateur LLM à base de prompt n’a pas les mêmes paramètres d’initialisation).

## 7 — Notre mesure : mêmes 360 jugements par juge

**04:15 → 05:10 · 55 s · la course rejoue les médianes à l’échelle réelle**

Qu’avons-nous mesuré ? Les mêmes trois cent soixante jugements par juge, sur trois rubriques. La course rejoue nos médianes à l’échelle réelle : quatre cent quatre-vingt-dix-neuf millisecondes pour Decision via la Function, trois mille cinq cent quarante-six pour gpt-5.4, soit environ sept fois plus rapide. Ce n’est pas du live. En estimation retail, soixante-dix-sept millièmes de dollar contre cinq dollars vingt-huit : environ soixante-neuf fois moins, hébergement exclu.

Mais nous n’avons pas de labels humains : le tableau donne un accord entre juges, pas une exactitude. Decision est plus indulgent sur la fidélité et la pertinence, plus sévère sur la complétude, où l’accord est le meilleur. Prochaine étape : cent à deux cents cas annotés pour régler les seuils. Les études indépendantes citées portent sur Jev, pas sur Decision-1.

**Précisions et sources.** Protocole : graine 42, 40 questions sur 130, mêmes questions pour toutes les configurations, 120 réponses Cosmos-rerank (Sol, Terra, Luna) déjà stockées et réutilisées sans régénération, 3 rubriques × 2 juges = 720 jugements en 3 runs, 0 erreur. Latences : 45 sondes directes séquentielles par juge ; médianes 499 ms (Decision via Function) et 3 546 ms (gpt-5.4), p95 1 163 et 5 160 ms. Côté serveur Decision, n = 360 : 199 / 406 ms ; la latence serveur du LLM n’est pas disponible, et on ne mélange jamais serveur et client. Coût : estimation retail d’après les tokens observés (≈ 1,83 M en entrée côté Decision ; ≈ 1,50 M en entrée et 102 k en sortie côté LLM) × tarifs 0,042 $/M (sortie gratuite) contre 2,50 $/M en entrée et 15 $/M en sortie, soit 0,077 $ contre 5,28 $ (0,21 $ contre 14,67 $ pour 1 000 jugements), hébergement Flex et Blob/PE/DNS hors total (~7,80 $/mois). Accord sur 120 paires par rubrique : Spearman 0,41 / 0,36 / 0,75 ; taux de passage Decision / LLM 95/75, 97/87, 17/28. Contexte indépendant, Jev et non Decision-1 : Arize (23 325 jugements, seuil choisi sur une moitié puis testé sur l’autre, 76 → 87 %, coût 1/286) ; CMU, arXiv 2609.26550 (≤ 3 points sous GPT-6 quand le verdict se lit dans le texte, 0,36 % du coût).

## 8 — Le même motif, ailleurs

**05:10 → 06:05 · 55 s · six familles visibles, cycle de 7 s, vidéos à la demande**

Où ailleurs ? Le même motif revient dans six familles, pas dans une liste de secteurs. Gate : laisser passer ou faire approuver un appel d’outil. Route : choisir l’équipe ou le modèle suivant. Rank et Select : retenir la bonne preuve. Label : étiqueter à grande échelle. Judge : notre fil rouge. Control : choisir la prochaine action d’interface.

Chaque tuile porte un exemple, un chiffre et sa provenance : notre mesure, annoncé, auto-déclaré. La bande du dessous rejoue la même grammaire : état, question typée, réponse réelle de Decision-1, action du code. Les chiffres communautaires, Browser Use ou le filtre d’injection, viennent de Jev, pas de Decision-1. Trois démonstrations officielles sont à lancer à la demande.

**Précisions et sources.** Six appels réels (12 jugements) sur Decision-1 avec des états synthétiques non confidentiels : Gate (3 noul), Route (choice, score, noul), Rank (1 choice), Label (1 choice), Judge (3 scores), Control (1 choice). Total conservé : 36 jugements sur 14 appels avec les 24 antérieurs. Le coût du Gate est dérivé des tokens réels (estimation retail), pas une facture. Les actions du code sont une visualisation de politique : rien n’est exécuté. Rank retient un passage sur cinq, pas un reranking complet. Label : Xbox, plus de 10 000 retours = annonce du fournisseur, non reproduite chez nous. Control : 7,1 s = Browser Use + Jev, recherche de vols sur une seule tâche, petit LLM pour écrire le texte saisi, sans réservation. Computer use, 0,0002 $ contre 0,032 $ : auto-déclaré, état reconstruit par code. Filtre d’injection, 96,5 % sur 662 messages : jev-sec-bench, auteur indépendant, seuil 0,5, contexte fourni (89,7 % sans). Routeur Codex : modèle et effort, la simulation à −60 % n’est pas reprise. Pas de source primaire pour l’exemple « assistant voiture » : non retenu. Les trois clips sont officiels, jamais lancés automatiquement ; le clip computer-use compare Decision-1 associé à Sol et un agent Sol seul.

## 9 — Ce que ça fait bien, mal, et avec qui

**06:05 → 07:00 · 55 s · slide statique : pour, contre, qui fait quoi**

Quand ne pas l’utiliser ? Ce qu’il fait bien : plus rapide et moins cher dans nos mesures, une sortie typée qui ne peut pas inventer de label, une probabilité qu’on peut seuiller : chez Arize, régler le seuil fait passer la précision de 76 à 87 %. Ce qu’il fait moins bien : pas de justification, donc des erreurs confiantes possibles ; les textes très longs, où les appels hors limite comptent comme faux ; les séquences, 35 % contre 54,5 % sur ALFWorld ; et avec des labels, un classifieur fine-tuné reste devant.

Ce ne sont pas des remplaçants mais des rôles : règles, classifieur, modèle de décision, LLM, humain. Quand le modèle hésite, on peut escalader sous un seuil validé : à tester chez nous. Pour démarrer : annexe A.

**Précisions et sources.** Arize (Jev) : seuil choisi sur validation puis testé sur un split distinct, 76 → 87 % ; 34 % des réponses fidèles dépassent 0,5 ; la cascade n’apporte rien après réglage du seuil. CMU : cascade +0,9 point à 41 % du coût de GPT-6 (1 610 paires, seuil gelé). Scale AI : pplx-decider → Opus sur LLMBar, 17 % d’escalade, ce n’est pas Decision-1. arXiv 2610.00346 : DeBERTa fine-tuné en tête sur presque tous les jeux étiquetés. Contexte long : dans 2611.00505, les appels hors limite de Decision-1 comptent comme faux ; ne pas confondre avec l’exactitude conditionnelle. L’escalade vers un LLM ou un humain n’a pas été testée chez nous. Les taux de bascule de protocoles différents ne se comparent pas.

## A · Lundi matin : un pilote mesuré

**Annexe A · hors chrono**

Que faire lundi ? Un : une décision précise, des options fermées et une option d’abstention. Deux : cent à deux cents exemples annotés, pour régler les seuils plutôt que garder 0,5. Trois : une cascade sous le seuil, vers un LLM ou un humain. Quatre : figer modèle, version et taxonomie, et auditer un échantillon des réponses confiantes. Cinq : mesurer coût et latence sur notre propre charge. Le modèle mesure, le code décide, le LLM écrit.

**Précisions et sources.** 100–200 est un point de départ de pilote, pas une garantie statistique pour une décision à fort enjeu ou des erreurs rares. Régler les seuils sur validation, mesurer sur un test distinct, choisir le coût des faux positifs et des faux négatifs, prévoir la dérive. Enregistrer la version de déploiement et la taxonomie ; ne pas inférer une version détaillée du seul champ model retourné. Aucun déploiement, RBAC, clé ou ressource Azure n’a été modifié pour ce support.

## B · code direct, erreurs et délais

**Annexe B · hors chrono**

Le corps téléchargé est l’appel réel de la slide 4 (ticket de support, trois questions : choice, score, noul). Le code utilise l’origine HTTPS de la ressource, le nom du déploiement, l’audience Entra correcte, des délais et des erreurs explicites. Le diagramme éditable est généré en SVG et PNG depuis la source draw.io ; les icônes proviennent de la collection officielle Microsoft.

**Précisions et sources.** Code Python compilé et testé sans réseau avec les 14 réponses capturées et des erreurs HTTP simulées. Le fichier ne crée aucune ressource ni permission. Un résultat peut être validé sans que la confiance du modèle soit prise pour de l’exactitude.

## C · 36 jugements réels conservés

**Annexe C · hors chrono**

Les huit captures antérieures sont inchangées ; leur hash canonique a été vérifié avant et après l’ajout. Le nouveau lot compte six appels et douze questions, très en dessous du plafond de soixante nouvelles questions. Aucune donnée client n’a été envoyée à l’API.

**Précisions et sources.** Les latences des nouveaux appels sont celles du client HTTP direct, hors acquisition du jeton, et non celles de l’adaptateur : ce n’est pas un benchmark. Les réponses moins attendues sont conservées, y compris les probabilités promises_refund du cas gate.

## D · protocole Foundry et résultats complets

**Annexe D · hors chrono**

Les trois configurations ne sont pas classées statistiquement. L’ancienne rubrique FideliteSources est distincte : Spearman 0,12 avec Decision, 0,18 avec le nouveau juge LLM.

**Précisions et sources.** Moyennes par réponse Sol / Terra / Luna, Decision / LLM : fidélité 93,0 / 82,6 ; 95,5 / 88,0 ; 91,7 / 83,4. Pertinence 95,0 / 95,9 ; 94,5 / 86,4 ; 94,7 / 95,6. Complétude 56,4 / 63,1 ; 46,5 / 51,1 ; 59,7 / 62,4. Les données confidentielles d’origine ne figurent pas dans le support.

## E1 · table des preuves

**Annexe E1 · hors chrono**

Les étiquettes séparent nos mesures, les études indépendantes, les auto-déclarations et les annonces. Le périmètre de chaque chiffre est nécessaire à toute réutilisation.

**Précisions et sources.** Arize ne constate pas de gain à escalader une fois le seuil ajusté ; d’autres papiers en constatent, dans des protocoles précisément décrits. Les exemples communautaires tournent avec Jev, pas avec notre déploiement Decision-1.

## E2 · table des preuves

**Annexe E2 · hors chrono**

Les étiquettes séparent nos mesures, les études indépendantes, les auto-déclarations et les annonces. Le périmètre de chaque chiffre est nécessaire à toute réutilisation.

**Précisions et sources.** Arize ne constate pas de gain à escalader une fois le seuil ajusté ; d’autres papiers en constatent, dans des protocoles précisément décrits. Les exemples communautaires tournent avec Jev, pas avec notre déploiement Decision-1.

## F1 · sources publiques et mesures indépendantes

**Annexe F1 · hors chrono**

Publications et documents d’API ne prouvent pas à eux seuls une exactitude sur notre trafic. Les travaux qui étudient Jev ou d’autres System-1 ne doivent pas être réétiquetés Decision-1.

**Précisions et sources.** Tous les résumés sont originaux ; aucun code public exécuté, aucun compte tiers utilisé. L’étude Scale AI (2611.00505) est la seule des quatre études indépendantes à évaluer Decision-1 comme juge ; Arize, CMU et 2610.00346 portent sur Jev ou d’autres System-1.

## F2 · projets communautaires

**Annexe F2 · hors chrono**

Les projets communautaires utilisent Jev (TypeSafe AI), pas Decision-1 : ce sont des exemples du motif, pas des mesures de notre modèle. Le détail de la loss, des données et de la calibration de Decision-1 demeure non documenté.

**Précisions et sources.** OpenJev reste distinct : pas de LoRA ni de scalar head attribué à Decision-1.

## G · Decision-1 dans la boucle, vidéos officielles

**Annexe G · hors chrono**

Les trois vidéos proviennent de notre article officiel. Durées et images ont été vérifiées dans un navigateur local : classification 31,77 s, computer-use 28,96 s, Discovery 16,02 s. Le clip computer-use associe bien Decision-1 à Sol : il ne montre pas que le modèle de décision remplace toute génération.

**Précisions et sources.** Les trois vidéos s’ouvrent au clic dans un cadre pointant vers commandline.microsoft.com ; rien n’est copié dans le dépôt. Les projets Jev liés utilisent leurs propres modèles et protocoles ; nous n’avons pas exécuté leurs agents, utilisé leurs comptes ou reproduit leurs promesses. Nos trois vidéos locales précédentes restent des essais locaux archivés, non utilisées comme preuve générale.

## H · mécanisme : documenté / non documenté

**Annexe H · hors chrono**

Le dessin d’exécution de la slide 2 oppose des contrats et une boucle de rédaction ; il ne spécifie pas les internes non publiés du scoring. Le softmax n’est pas supprimé par principe. Nous ne savons pas si sa normalisation effective porte sur tout le vocabulaire ou seulement sur des labels, ni combien de passes internes servent plusieurs options ou plusieurs questions. La slide 3 et l’annexe I reprennent ces limites côté entraînement.

**Précisions et sources.** Sources publiques : article Command Line (10/10/2026) et documentation Learn. Les entrées des douze nouvelles questions sont strictement synthétiques et non confidentielles. Les sources sont citées dans les annexes F1 et E1/E2.

## I · entraînement des modèles de décision

**Annexe I · hors chrono**

Cette annexe soutient la slide 3. Elle compare les approches publiques pour entraîner un modèle de décision. Le point commun : un LLM ou un encodeur pré-entraîné, puis un entraînement sur des décisions avec un résultat connu, avec une perte qui récompense la justesse des probabilités (Brier, log-loss) plutôt qu’une préférence humaine.

**Précisions et sources.** Le guide de synthèse de Roitman (arXiv 2610.33775 v2, auteur unique, source tierce) rapporte quatre principes de RLCD : objectif de calibration, règles de score propres, tâches à résultat vérifiable, aucun texte libre. D’après l’annuaire systemonemodels.org, TypeSafe n’a publié ni récompense, ni données, ni code : ces principes ne constituent pas une recette reproductible. Ces deux sources se contredisent sur ce qui est publié ; nous présentons donc l’objectif et la famille de récompense comme attribués, pas comme établis. Le guide range Microsoft-Decision-1 dans le tableau des modèles commerciaux avec « Qwen3.5-9B + RLCD post-training » ; sa référence est l’article Microsoft, qui dit seulement « post-entraîné pour un scoring de décision en un passage ». Ne pas affirmer que Decision-1 est entraîné avec RLCD. Autres approches du guide : SFT, modèles de récompense par préférences, apprentissage contrastif, distillation (perte croisée + KL), entraînement multitâche et curriculum. Clef : source Cloudflare relayée par systemonemodels.org (auto-déclaré). Note : le guide décrit Jev comme un encodeur bidirectionnel, alors que l’annuaire note que l’architecture n’est pas divulguée ; ne pas citer cette architecture comme un fait.

## Références

- [Notre publication Decision-1](https://commandline.microsoft.com/microsoft-decision-1-model-foundry/) [annoncé] — 10 oct. 2026. Base Qwen3.5-9B post-entraînée au scoring en un passage ; prix Foundry ; clips officiels. Benchmarks du fournisseur.
- [Decision API — Learn](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision) [documenté] — Format de requête (state, questions typées), probabilités, score = moyenne pondérée des indices ; endpoint et déploiement.
- [Foundry endpoint evaluators](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/custom-evaluators#endpoint-based-evaluators) [documenté] — Contrat schema_version 0.0.1, réponse score/status/properties, authentification par connexion. Preview, délai de 30 s.
- [Notre accélérateur](https://github.com/dorianb04/foundry-decision-eval-accelerator) [notre code] — Code lu sans modification : définition d’évaluateur endpoint et testing_criteria. Nos rapports détaillés ne sont pas dans le dépôt.
- [Arize — Jev as a judge](https://arize.com/blog/jev-as-a-judge/) [mesuré indépendant] — 23 325 jugements (Jev). Seuil réglé sur une moitié, testé sur l’autre : 76 → 87 %. 34 % des réponses fidèles > 0,5.
- [JEV-as-a-Judge, v6](https://arxiv.org/abs/2609.26550v6) [mesuré indépendant] — ≈ 3 points sous GPT-6 quand le verdict se lit dans le texte, à 0,36 % du coût ; plus faible en calcul, code, logique.
- [Classifieurs et decision gates](https://arxiv.org/abs/2610.00346v2) [mesuré indépendant] — Avec des labels, DeBERTa-v3 fine-tuné est le plus précis sur presque tous les jeux ; première étape + Gemma ≈ prix de Jev.
- [14 System-1 judges, 11 benchmarks](https://arxiv.org/abs/2611.00505v1) [mesuré indépendant] — Inclut Decision-1 parmi 14 modèles. Cascade à 17 % d’escalade : pplx-decider → Opus, pas Decision-1. Échecs de contexte comptés faux.
- [Jev — évaluation sur 37 jeux](https://arxiv.org/abs/2609.37647v2) [mesuré indépendant] — ALFWorld : Jev 35 %, Qwen 54,5 %. Actions parfois répétées sans effet. Ni notre déploiement ni Decision-1.
- [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) [auto-déclaré] — Jev choisit l’opération depuis un état DOM ; un petit LLM écrit seulement le texte saisi. Vols en 7,073 s, une tâche, sans réservation.
- [awlevin/typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use) [auto-déclaré] — $0,0002 contre $0,032 par décision, mais le code reconstruit les faits que le modèle frontière lit dans les pixels. Auto-déclaré.
- [jev-sec-bench](https://github.com/Gaurav-Gosain/jev-sec-bench) [mesuré indépendant] — Auteur indépendant : 96,5 % sur 662 messages (seuil 0,5, contexte fourni), 13 faux négatifs. Jev, pas Decision-1.
- [Jev Codex Router](https://github.com/0xNatoshi/jev-codex-router) [auto-déclaré] — Jev choisit modèle et effort avant l’appel LLM. L’économie de −60 % vient d’une simulation : non reprise ici.
- [droidrun/mobile-jev](https://github.com/droidrun/mobile-jev) [auto-déclaré] — Jev pilote des actions mobiles ; sélection du paiement en ~21 s et 9 actions ; aucune réservation achevée.
- [HEIST//ONE](https://github.com/AbdelStark/heist-one) [auto-déclaré] — Jev propose des jugements de gardes ; le code déterministe garde physique et état. Un run live : 12 requêtes, 288 questions.

## Périmètre mesuré : réel, rejoué, illustratif

**Réel.** 12 nouveaux jugements sur 6 appels HTTP 200, sans réessai, ajoutés aux 24 antérieurs : **36 jugements conservés sur 14 appels**. Les huit captures antérieures sont inchangées (hash vérifié). Corps, sorties, usage, latence HTTP et heure UTC sont dans [le JSON de captures](decision-captures.json). La voie droite de la slide 2, la slide 4 (ticket de support) et les bandes de la slide 8 affichent ces réponses telles quelles. Les trois vidéos sont les clips officiels de notre article.

**Rejoué.** La course de la slide 7 rejoue nos médianes mesurées (499 ms et 3 546 ms, sondes n = 45) à l’échelle de temps réelle ; coûts et accords viennent du rapport Foundry initial. Ce n’est pas du live : aucune inférence n’est lancée à l’ouverture ni pendant la présentation.

**Illustratif.** Les logits et probabilités de la voie gauche de la slide 2, les contrats 4/5 et 0,82 de la slide 5 et les « actions du code » de la slide 8 sont des visualisations, pas des mesures.

## Crédits

Direction visuelle inspirée de Command Line (grille éditoriale, étiquettes monospace, filets fins) ; CSS et JS originaux, polices système locales. Animations CSS + Web Animations API, sans CDN ni framework. Architecture éditable draw.io, SVG et PNG générés depuis cette source, icônes exclusivement issues de la collection officielle Microsoft (aka.ms/MsiconsCollections).

**Sources :** publiques uniquement (article, documentation, études tierces) et mesures de notre projet ; aucune clé, aucun secret.
