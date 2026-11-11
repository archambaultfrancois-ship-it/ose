#include "component.h"
#include <inttypes.h>
#include <stdio.h>

/* Chaque composant tourne dans son propre processus. */
static uint64_t prochain = 1;
static uint64_t attente = 0;

static int on_horloge(OseContext *ctx, const Input_horloge *input) {
    (void)input; /* Un timer ne transporte pas de message SDL. */

    if (attente != 0) {
        OseSelector choix = {.has_app_id = 1, .app_id = attente};
        Input_reponses lecture, retrait, verification;

        if (!get_reponses(ctx, &choix, &lecture)) return 0;
        if (!lecture.message) return 1; /* Reessayer au prochain timer. */
        if (lecture.message->valeur != 2 * attente)
            return ose_fail(ctx, "resultat incorrect");
        printf("[get] id=%" PRIu64 " : valeur=%" PRIu32 "\n",
                attente, lecture.message->valeur);

        /* get a conserve la reponse : pull peut encore la lire et la retirer. */
        if (!pull_reponses(ctx, &choix, &retrait)) return 0;
        if (!retrait.message) return ose_fail(ctx, "reponse introuvable au pull");
        if (!get_reponses(ctx, &choix, &verification)) return 0;
        if (verification.message) return ose_fail(ctx, "reponse encore presente");
        printf("[pull] id=%" PRIu64 " : retiree, get suivant vide\n", attente);
        attente = 0;
    }

    if (prochain > 3) {
        printf("[fin] 3 resultats verifies : 2, 4, 6\n");
        return ose_disarm(ctx, "horloge");
    }

    Nombre requete = {0};
    requete.valeur = (uint32_t)prochain;
    if (!publish_requete_with_id(ctx, &requete, prochain)) return 0;
    printf("[timer] envoi id=%" PRIu64 " : valeur=%" PRIu32 "\n",
            prochain, requete.valeur);
    attente = prochain;
    prochain++;
    return 1;
}

const ComponentCallbacks component_callbacks = {.on_horloge = on_horloge};
