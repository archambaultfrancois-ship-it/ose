# Développer et intégrer une application OSE avec des sous-systèmes

Un sous-système est une définition réutilisable de composants, d'abonnements et
d'une interface publique. Il peut être construit et testé avec un banc autonome,
puis instancié dans une application sans modifier son modèle interne. Une même
application peut mélanger des composants locaux et des sous-systèmes imbriqués.

Ce guide décrit les commandes CLI, l'édition graphique des interfaces,
l'extraction de composants en sous-système et la navigation hiérarchique dans la
console. La [référence technique](../ose/docs/subsystems.md)
définit les formats et les limites ; le [glossaire](../GLOSSARY.md) donne les
termes anglais.

## Choisir une frontière

Un sous-système doit pouvoir être décrit et testé par ses échanges publics.
Par exemple, Détection reçoit des mesures, expose des alarmes et possède un
bloc de paramètres définissant son seuil. Les filtres et détecteurs internes
restent privés pour les autres modèles.

```text
Acquisition → mesures → Détection → alarmes → Supervision
```

La frontière organise les sources et les contrats. Elle ne crée aucun processus,
callback, délai ou file supplémentaire. Les composants élémentaires continuent
d'être exécutés par le moteur habituel. Les permissions des clients externes
restent définies par le déploiement ; l'encapsulation du modèle ne constitue pas
une isolation de sécurité à l'exécution.

## Créer et tester un sous-système autonome

Depuis le checkout, préparer l'environnement comme dans le
[démarrage rapide](quick-start.md), puis créer un projet hors des sorties de build :

```sh
ose subsystem init mon-detecteur
ose subsystem validate mon-detecteur
ose subsystem test mon-detecteur --workers 2
```

Le squelette est un traitement Python qui recopie ses entrées. Son scénario
injecte trois valeurs et son test vérifie leur ordre. `init` exige un dossier
nouveau ou vide et n'écrase pas de projet existant.

```text
mon-detecteur/
  subsystem.json
  model.json
  deployment.json
  schemas/value.sdl
  components/processor.py
  tests/
    harness.json
    nominal/scenario.json
    nominal/expectations.json
```

`subsystem.json` désigne les documents internes et expose leurs ports :

```json
{
  "version": 1,
  "model": "model.json",
  "deployment": "deployment.json",
  "interface": {
    "inputs": {"in": {"to": "processor.input"}},
    "outputs": {"out": {"from": "processor.out"}}
  }
}
```

Les contrats SDL viennent des ports internes exposés. L'interface est un alias,
sans recodage du message. Pour commencer, une entrée publique désigne une entrée
interne et une sortie publique désigne une sortie interne.

## Écrire les stimuli et les attentes

Un scénario de banc contient une durée simulée en secondes et des événements :

```json
{
  "version": 1,
  "duration": 0.5,
  "events": [
    {"at": 0, "input": "in", "value": {"value": 20}},
    {"at": 0.1, "input": "in", "value": {"value": 25}},
    {"at": 0.2, "input": "in", "value": {"value": 90}}
  ]
}
```

L'injecteur encode ces valeurs avec le codec SDL du contrat d'entrée. L'absence
d'événement représente un silence. Une erreur de type ou de valeur est rejetée
avant le lancement. Un injecteur ne simule pas les réactions métier d'un voisin :
une telle réaction demande une doublure comportementale.

Le collecteur est un composant OSE générique abonné à la sortie publique. Il
enregistre le message effectivement reçu et l'instant simulé du début de son
callback. Cela établit sa réception par le banc, pas son traitement par un autre
consommateur de l'application.

Pour une détection qui publie seulement au-dessus du seuil :

```json
{
  "version": 1,
  "assertions": [
    {"output": "out", "from": 0, "until": 0.2, "count": 0},
    {"output": "out", "from": 0.2, "until": 0.25,
     "count": 1, "value": {"value": 90}}
  ]
}
```

Les fenêtres sont **[from, until)** : début inclus, fin exclue. Les vérifications
peuvent compter les messages, rechercher des champs, vérifier une séquence et
appliquer une tolérance numérique. Le premier lot exprime les délais par fenêtres
absolues du scénario ; il ne fournit pas encore de langage de corrélation entre
événements.

Pour explorer sans vérifier d'attentes :

```sh
ose subsystem run mon-detecteur --case nominal --mode simulated
```

Un cas d'observation peut omettre `expectations`. `test` exige au moins une
assertion par cas.

## Choisir les voisins avec un profil d'intégration

Le fichier `tests/harness.json` associe des profils aux cas :

```json
{
  "version": 1,
  "profiles": {
    "standalone": {"cases": ["nominal"]},
    "with-acquisition": {
      "model": "with-acquisition.model.json",
      "deployment": "with-acquisition.deployment.json",
      "inject": [],
      "cases": ["integration"]
    }
  },
  "cases": {
    "nominal": {
      "scenario": "nominal/scenario.json",
      "expectations": "nominal/expectations.json"
    },
    "integration": {
      "scenario": "integration/scenario.json",
      "expectations": "integration/expectations.json"
    }
  }
}
```

Le profil autonome est généré autour d'une instance nommée `sut` : injecteurs
sur ses entrées et collecteurs sur ses sorties. Un profil avec des voisins
fournit un modèle et un déploiement ordinaires. Ce modèle doit instancier le
sous-système ciblé sous le nom `sut` ; les autres composants ou sous-systèmes
peuvent être réels ou être des doublures écrites avec les SDK OSE.

Par défaut, les entrées de `sut` déjà raccordées dans le modèle de banc ne
reçoivent pas d'injecteur. `inject` et `collect` permettent une sélection explicite.
Un scénario référençant une entrée non injectée échoue, au lieu d'ignorer ses
stimuli. Les profils peuvent donc sélectionner des cas adaptés à leurs voisins.

```sh
ose subsystem test ose/examples/subsystems/detection
ose subsystem test ose/examples/subsystems/detection \
  --integration-profile with-acquisition
```

OSE assemble les éléments déclarés ; le développeur écrit les scénarios,
doublures et résultats attendus. Le profil d'intégration choisit le graphe du
banc. `--profile local|cluster` garde son sens distinct de profil de déploiement ;
les tests de ce premier lot exécutent un déploiement local en simulation.

## Instancier et imbriquer dans l'application

Dans le modèle principal, ajouter `subsystems` et relier les ports publics au
moyen des `subscriptions` habituelles :

```json
"subsystems": {
  "gauche": {"source": "../detection/subsystem.json"},
  "droite": {"source": "../detection/subsystem.json"}
}
```

Chaque instance reçoit ses propres composants, connexions, paramètres et état.
Les composants locaux restent dans `components`. Les abonnements du parent
peuvent viser `gauche.in` ou partir de `gauche.out`, mais pas accéder à
`gauche.processor.input`.

Un sous-système peut lui-même déclarer `subsystems`. Son interface peut exposer
les ports publics d'un enfant. `ose build` résout tous les niveaux et importe
leurs connexions internes. Les cycles de définitions sont rejetés ; les boucles
de messages suivent les règles ordinaires d'activation d'OSE.

```sh
ose build --model ose/examples/subsystems/model.json \
  --deployment ose/examples/subsystems/deployment.json \
  --build-dir build/subsystems-application
ose run --plan build/subsystems-application/plan.json \
  --mode simulated --duration 0.35
```

L'[exemple complet](../ose/examples/subsystems/README.md) conserve un affichage
local et imbrique Détection dans Surveillance. Le déploiement principal ne
redéclare pas les composants internes. Il peut sélectionner un autre déploiement
par instance et adapter explicitement son environnement ou son placement.

## Paramètres et travail en équipe

Exposer un bloc avec `interface.parameters`, puis utiliser `parameters` dans
l'instance pour remplacer sa valeur initiale publique. Les blocs internes sont
préfixés par le chemin d'instance, comme `gauche/settings`. Pour un partage
explicite, `parameter_bindings` raccorde un paramètre public à un bloc déclaré
par le parent ; les contrats doivent être compatibles dans les deux sens.

Dans un banc, un événement `parameter` peut remplacer un bloc public à un instant
simulé. L'exemple Détection vérifie ainsi un changement de seuil.

Une équipe peut conserver son sous-système dans un dossier ou un dépôt séparé.
Les références sont des chemins locaux explicites : le premier lot n'effectue
aucun téléchargement ni résolution de versions distante. Pour reproduire une
intégration, fixer les révisions des dépôts par les outils de gestion de sources.

## Composer avec l'éditeur graphique

Depuis le dossier de l'exemple, dans Bash :

```bash
cd ose/examples/subsystems
source ../../../setenv.sh
ose editor --root .
```

L'éditeur charge les définitions, modèles, déploiements et schémas référencés.
Un sous-système apparaît comme un bloc avec ses seuls ports publics. Les
connexions graphiques et **Connect ports** utilisent ces ports comme ceux d'un
composant local. **Open definition**, ou un double-clic sur le bloc, ouvre le
modèle interne ; le fil d'Ariane permet de remonter. Le bandeau indique que
l'édition modifie la définition partagée, donc toutes ses instances. Chaque
modèle conserve sa disposition séparément.

Le sélecteur **Deployment** en haut choisit le profil de l'application complète.
Dans une définition, **Definition deployment** choisit le déploiement dont les
propriétés sont éditées. **Duplicate deployment** crée un profil local à ce
modèle. Pour l'utiliser dans l'application, le sélectionner explicitement dans
le champ `subsystems.<instance>.source` du déploiement parent.

**Instantiate subsystem** ajoute une instance d'une définition existante ; son
chemin est relatif au modèle affiché. **New subsystem** prépare une définition
vide avec son modèle et son déploiement. Ouvrir ensuite cette définition pour
ajouter ses composants. Les valeurs initiales publiques et les liaisons de
paramètres se modifient dans l'inspecteur de l'instance.

Dans une définition, **Expose public port** raccorde un nom public à une entrée,
une sortie ou un bloc de paramètres. Une entrée ou sortie peut viser le port
public d'un enfant. Renommer un composant ou un port interne conserve ces
raccordements ; supprimer une cible exposée demande d'abord de retirer son
exposition dans l'interface.

Pour convertir une application existante, sélectionner des composants locaux
puis choisir **Extract selection**. L'aperçu montre les nouveaux fichiers,
l'interface déduite des connexions traversant la frontière, les abonnements
internes et les déploiements de chaque profil enregistré. Les handlers restent
à leur emplacement actuel et leurs chemins sont adaptés. Les blocs de
paramètres partagés restent liés explicitement au parent. Les chemins de
schémas et les substitutions `{project}` et `{generated}` conservent leur
contexte ; les références adaptées restent relatives au projet.

Ces opérations restent dans le brouillon jusqu'à **Save** et sont annulables.
**Validate** vérifie l'application complète, y compris les modifications non
sauvegardées des définitions imbriquées, sans exécuter les handlers. Les
répertoires de dépendances extérieurs au projet doivent être enregistrés par
`--source-root` au lancement de l'éditeur.

## Observer la hiérarchie dans la console

Après avoir reconstruit le plan et relancé l'application, la vue **Application
flow** de la console affiche les chemins logiques des composants. **Subsystem**
et les boutons **Open** permettent d'entrer dans les instances et leurs enfants ;
**Parent subsystem** remonte d'un niveau. Les voisins immédiats restent visibles
pour conserver les liens traversant la frontière. La sélection, les métriques,
les diagnostics et l'observation des messages continuent d'utiliser les
identifiants des composants exécutés, désormais identiques aux chemins logiques
(`cell_1.plant`). Le port est séparé par `:`, par exemple
`cell_1.plant:frame`, et les mesures portent des noms comme
`cell_1.plant:temperature`. Reconstruire les anciens plans à identifiants `ss_`
et recréer les sélections ou permissions utilisant ces identifiants.
Les anciens plans sans métadonnées de
composition gardent la vue plate.

## Exploiter les résultats en CI

```sh
ose subsystem test mon-detecteur --workers 2 \
  --report junit --output build/detection-junit.xml
```

Le code de sortie vaut 0 si les cas réussissent, 1 pour une assertion échouée,
2 pour une erreur de configuration/construction et 3 pour une erreur d'exécution
ou de collecte. Une interruption clavier utilise le comportement CLI habituel.

Les sorties sont conservées par exécution sous `build/subsystem/case-…/` : modèle
et déploiement résolus, plan, stimuli encodés, trace, collectes JSONL,
`outputs.json` et `result.json`. `results.json` rassemble le dernier lot de cas.
Une collecte perdue, incomplète ou dépassant les budgets ne peut pas valider un
test. Les fichiers de collecte conservent les entiers JSON exacts et refusent les
flottants non finis. Les instantanés de déploiement peuvent contenir des secrets,
comme un plan OSE ordinaire : sélectionner les artefacts publiés par la CI.
