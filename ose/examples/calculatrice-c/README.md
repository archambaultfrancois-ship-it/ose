# Calculatrice OSE en C

Deux composants C, trois nombres à doubler, un timer, une entrée Push et une
entrée Pull avec sélection par identifiant.

Le [tutoriel complet en français](../../../docs/tutoriel-application-c.md)
part de la spécification, fait recréer chacun de ces fichiers dans un dossier
vide, puis décrit la compilation, la simulation et l'exécution locale réelle.

Pour exécuter directement cet exemple, depuis la racine du dépôt, dans Bash :

```sh
cd ose/examples/calculatrice-c
unset OSE_APP_ROOT
source ../../../setenv.sh
ose validate --model model.json --deployment deployment.json
ose build --model model.json --deployment deployment.json
ose run --plan build/plan.json --mode simulated --duration 5 --workers 1 \
  --trace build/trace.jsonl
ose run --plan build/plan.json --mode realtime --duration 5
```

Chaque exécution doit afficher `[fin] 3 resultats verifies : 2, 4, 6` sur stdout.
Le timer se désarme après le dernier résultat. La simulation s'arrête alors par
quiescence ; l'exécution réelle reste active jusqu'à la durée demandée.
