#include "component.h"
#include <inttypes.h>
#include <stdio.h>

static int on_requete(OseContext *ctx, const Input_requete *input) {
    Nombre reponse = {0};
    if (!input->message || !input->meta.has_app_id)
        return ose_fail(ctx, "requete sans message ou sans identifiant");

    reponse.valeur = 2 * input->message->valeur;
    printf("[push] id=%" PRIu64 " : %" PRIu32 " -> %" PRIu32 "\n",
            input->meta.app_id, input->message->valeur, reponse.valeur);
    return publish_reponse_with_id(ctx, &reponse, input->meta.app_id);
}

const ComponentCallbacks component_callbacks = {.on_requete = on_requete};
