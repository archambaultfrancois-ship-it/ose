# Transition vers des publications ouvertes et le rejeu Vibration

Statut : transition implémentée et qualifiée. Les guides OSE et
Vibration décrivent les commandes disponibles. Ce document conserve le découpage
et les critères de transition. La notion de domaine est exclue du périmètre.

## Cible fonctionnelle

Le modèle décrit les composants prévus, les contrats et les abonnements. Il ne
constitue pas une liste fermée des producteurs admis. Un producteur absent du
modèle peut annoncer un contrat SDL compatible et alimenter un topic existant,
y compris une sortie de composant, sans modifier ni recompiler le modèle.

Plusieurs producteurs peuvent publier simultanément sur un topic. Leur admission
ne dépend pas de l'arrêt du producteur décrit dans le modèle. La désactivation
d'un composant est une fonction de supervision distincte, utile notamment pour
éviter de mélanger données générées et données enregistrées pendant un rejeu.

`external_topics` conserve son rôle de déclaration d'un contrat lorsque celui-ci
n'est fourni par aucune sortie de composant. Les points internes d'enregistrement
et les services de paramètres ne deviennent pas des sorties applicatives.

## 1. Fixer les invariants et les contrats de publication

- Séparer topic, producteur et génération de connexion. Une déclaration de
  producteur enregistre sa présence et son contrat ; elle ne réserve pas le topic.
- Distinguer le contrat déclaré dans le plan du contrat de chaque producteur.
  L'arrivée d'un producteur ne remplace pas le contrat global du topic.
- Vérifier le contrat du producteur contre les exigences des consommateurs et
  des liens. Un même nom de type ne suffit pas. Vérifier aussi chaque message.
- Conserver l'information de schéma nécessaire au décodage. Lorsque le chemin
  actuel suppose le schéma du producteur compilé, adapter la livraison au contrat
  du consommateur ou transporter le catalogue effectivement utilisé. Fixer ce
  choix dans la spécification du protocole avant les lots distribués et SDK.
- Identifier les publications par producteur/génération/séquence. Des producteurs
  distincts utilisant le même `app_id` ne produisent pas des doublons techniques.
- Conserver les files et la QoS du lien : FIFO selon l'ordre d'admission, latest
  sur l'ensemble des producteurs, capacités partagées, ACK et retransmissions.
  Aucun ordre global entre machines n'est promis.
- Une déconnexion libère uniquement le producteur concerné. Les données déjà
  admises peuvent se vider normalement ; les autres producteurs restent actifs.

Validation : spécification des cas de compatibilité SDL, concurrence,
déconnexion et reconnexion, avec exemples reproductibles indépendants de Vibration.

## 2. Ouvrir les publications dans le broker local

Points de départ : `ose/src/ose/execution/sessions.py` et `broker.py`.

- Remplacer la relation topic → propriétaire unique par un registre des
  producteurs et de leurs contrats, y compris les producteurs du plan.
- Retirer le refus fondé sur l'appartenance du topic à un composant et le refus
  d'un second producteur compatible. Conserver la vérification de déclaration
  dans la session et les limites de ressources.
- Adapter déclaration, publication, libération et nettoyage de session.
- Conserver les routes existantes vers les composants et les clients abonnés.
- Adapter observation, état retained/latest et enregistrement : la disparition
  d'un producteur ne doit pas invalider l'ensemble du topic ni masquer les autres.
- Exposer la liste ou le nombre de producteurs sans supprimer les champs de
  diagnostic existants qui restent pertinents.

Validation : un producteur hors modèle et le producteur prévu publient sur le
même topic ; le consommateur reçoit les deux. Un contrat incompatible est refusé.
La libération de l'un ne perturbe ni l'autre ni l'enregistrement. Tester TCP,
UDP, FIFO/latest, saturation, reconnexion, déduplication et identifiants applicatifs.

## 3. Qualifier les SDK et maintenir la compatibilité

- Conserver autant que possible `client.publisher(topic, Type)` et la libération
  du handle : une libération concerne ce producteur, pas tout le topic.
- Vérifier les SDK Python, Rust, C, Java et Ada, ainsi que les consommateurs
  lorsque plusieurs contrats compatibles alimentent le même topic.
- Si le protocole doit évoluer pour porter l'identité ou le catalogue, prévoir
  une négociation de capacité ou une version explicite. Ne pas modifier
  silencieusement le sens d'une enveloppe déjà utilisée par les anciens SDK.
- Mettre à jour les tests qui attendent aujourd'hui un refus d'exclusivité.
  Conserver les tests de contrats incompatibles et de sessions invalides.

Validation : tests avec deux programmes indépendants et tests de clients anciens
contre le nouveau broker dans le périmètre annoncé comme compatible.

## 4. Étendre la même sémantique au distribué

Point de départ : `ose/src/ose/execution/distributed.py`.

- Généraliser l'interface de publication extérieure, actuellement centrée sur le
  replay et les topics externes déclarés, aux producteurs ordinaires hors modèle.
- Définir les capacités correspondantes des SDK ; conserver l'authentification
  TLS et distinguer identité du client, topic et composant prévu dans le plan.
- Gérer plusieurs générations de producteurs simultanées, sans effacement des
  files ni révocation des producteurs voisins lors d'une arrivée ou d'un départ.
- Adapter les contrôles d'admission des workers : le nom du composant source
  compilé ne doit plus être l'unique identité possible du producteur effectif.
- Pour les publications extérieures, fournir une voie via le coordinateur vers
  chaque consommateur, y compris lorsque le lien du plan est en routage direct.
  Conserver les publications directes ordinaires sans livraison en double.

Validation : mêmes cas qu'en local, sur liens broker et directs ; contrats
incompatibles, reconnexions, publications de générations périmées et absence de
doublons. Une application minimale hors Vibration doit démontrer cette ouverture.

## 5. Ajouter une supervision ciblée des composants

Introduire la même notion d'état actif/inactif en local, en distribué et en
simulation. Ne pas utiliser la suspension système d'un processus comme protocole.

Interfaces implémentées :

```sh
ose run --disable-component sensor_1 --disable-component sensor_2
ose distributed-local --disable-component sensor_1 --disable-component sensor_2
ose component disable sensor_1 sensor_2
ose component enable sensor_1 sensor_2
ose component status
```

- Au démarrage, un composant désactivé ne lance aucun callback, y compris
  `on_start`, et ne bloque pas la disponibilité des autres composants.
- En cours d'exécution, la désactivation attend la fin du callback engagé puis
  suspend les callbacks et les timers. L'accusé confirme cet état effectif.
- Politique retenue : conserver l'état du composant suspendu, refuser les
  nouvelles admissions vers ses entrées pendant l'inactivité et vider ses entrées
  en attente à la transition. Les publications déjà admises en aval se vident
  normalement. Rendre cette politique visible et la tester.
- À la réactivation, reprendre l'état conservé et les délais restants des timers ;
  pour un composant jamais démarré, exécuter `on_start` une seule fois. Un arrêt
  puis redémarrage à froid reste une opération différente.
- Conserver le modèle, les contrats, les paramètres et les abonnements. En
  distribué, traiter explicitement les barrières de disponibilité et les routes
  vers un composant inactif.
- Exposer les états et les transitions ; rendre les demandes répétées idempotentes.

Validation : désactivation avant la première publication, pendant un callback,
réactivation, timers, files, paramètres accessibles, arrêt global et simulation
déterministe avec un ou plusieurs workers.

## 6. Faire du replay un producteur ordinaire

Points de départ : `ose/src/ose/recording/replay.py` et `live_replay.py`.

- Résoudre les cibles parmi les topics applicatifs connus, sorties comprises,
  et non exclusivement dans `external_topics`.
- Conserver `--map` pour sélectionner ou renommer les topics de l'enregistrement.
  Permettre les noms inchangés lorsque ceux-ci correspondent aux topics du plan.
- Réutiliser l'admission et la validation des producteurs ordinaires. Préserver
  `--schema-policy exact|compatible`, la prévalidation, les identifiants, les
  boucles, les bornes temporelles, les contrôles et l'enregistrement simultané.
- Permettre `--disable-component` au démarrage du replay simulé. En temps réel,
  la supervision porte sur l'application déjà démarrée : le replay ne réactive
  pas implicitement ses composants à la fermeture de sa connexion.
- En simulation, conserver le même plan et router les événements enregistrés
  vers les topics existants ; aucune réécriture persistée du graphe n'est nécessaire.

Validation : replay et producteur prévu actifs simultanément, puis replay seul
avec producteur désactivé ; mêmes règles de contrat et de livraison dans les trois
modes d'exécution. Préserver les anciens usages sur `external_topics`.

## 7. Appliquer la transition à Vibration Watch

- Utiliser le modèle normal et son plan local ou distribué habituel.
- Désactiver `sensor_1` et `sensor_2` avant leur première activation.
- Rejouer directement `sensor_1.signal` et `sensor_2.signal`. Les analyzers,
  l'alarme et l'observer conservent leurs liens et paramètres.
- Retirer la suppression des capteurs, la création de `replay.signal_*`, la
  réécriture des abonnements, la suppression des paramètres et le filtrage des
  permissions dans `prepare()`.
- Simplifier le lancement du replay et conserver les commandes Make existantes.
  `replay-build` peut rester une compatibilité vers la construction normale du
  profil sélectionné. Les traces peuvent rester dans un répertoire dédié.
- Mettre à jour les tests et la documentation qui attendent un graphe amputé.

Usage final, depuis Vibration Watch après construction normale :

```sh
# Rejeu simulé sur le plan normal, capteurs inactifs dès le démarrage.
ose replay signals.pcapng --plan build/plan.json --mode simulated \
  --disable-component sensor_1 --disable-component sensor_2

# Temps réel : premier terminal, application normale avec capteurs inactifs.
ose run --plan build/plan.json \
  --disable-component sensor_1 --disable-component sensor_2

# Second terminal : seuls les deux topics des capteurs sont sélectionnés.
ose replay signals.pcapng --plan build/plan.json \
  --map sensor_1.signal=sensor_1.signal \
  --map sensor_2.signal=sensor_2.signal
```

Pour le distribué, construire le profil cluster normal, démarrer
`ose distributed-local` avec les mêmes exclusions, puis connecter le replay au
port publié par ce lancement avec `--distributed --insecure` en développement.

Validation finale : comparer les résultats à ceux du replay actuel, avant de
supprimer ce dernier. Vérifier les alarmes, les valeurs, les `app_id`, les boucles,
les schémas compatibles, les points d'enregistrement internes et les traces
simulées identiques avec un et quatre workers. Les deux capteurs restent présents
dans le plan ; aucune donnée générée par eux ne doit apparaître pendant le rejeu.

## Ordre de livraison et critères de fin

Livrer les lots dans cet ordre : invariants, broker local, SDK, distribué,
supervision, replay, puis Vibration. Chaque lot doit disposer de tests et d'une
documentation avant le suivant ; la migration Vibration ne précède pas la
qualification générique. Les anciens chemins de replay restent utilisables
pendant la transition.

La transition est terminée lorsque des producteurs hors modèle alimentent des
liens existants avec plusieurs producteurs compatibles, que les trois moteurs
appliquent les mêmes règles, et que Vibration rejoue sans modèle ni plan spécial.


## Qualification réalisée

Qualification locale du 12 novembre 2026 :

- 360 tests unitaires réussis, dont suspension des timers, invalidation des
  accusés périmés et transport de grands catalogues d'enregistrement.
- 112 tests d'enregistrement/rejeu réussis : contrats exacts et compatibles,
  schémas multiples sur un topic, filtres, identifiants, contrôles temporels,
  points internes et captures locales/distribuées.
- Tests ciblés de publication et supervision réussis sur une application Python
  indépendante : deux producteurs sur une sortie du modèle, producteur prévu
  actif simultanément, contrat incompatible refusé, fermeture indépendante,
  démarrage différé, désactivation/réactivation et refus des entrées inactives.
  Le chemin distribué cible aussi un abonnement normalement direct.
- Régressions des modules distribués, entrées distribuées et simulation examinées ;
  les cas corrigés d'identifiants refusés et de traces ont été rejoués avec succès.
- Clients C, Rust et Java : partage d'une sortie du modèle et poursuite du
  producteur survivant vérifiés sans changement des API SDK. Java a été exécuté
  avec `OSE_JAVA_LOCAL_RELEASE=8` sur le JDK 21 local ; ceci ne qualifie pas Java 7.
  La qualification Ada n'a pas été exécutée, GNAT étant absent de cet environnement.
- `make test-replay` Vibration réussi : résultats/alarmes identiques à la capture,
  traces identiques avec un et quatre workers et avec capture interne activée,
  boucles, rejeu local et distribué sur le plan complet. Les points internes
  `analyzer_*.record.dsp.input` contiennent exactement les messages sélectionnés.

Le lanceur Vibration passe de 428 à 410 lignes. Le répertoire `build/replay`
conserve la trace ; le lanceur n’y construit plus de plan applicatif distinct.
