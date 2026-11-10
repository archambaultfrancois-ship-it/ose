#include "component.h"

static int on_input(OseContext *ctx, const Input_input *input) {
    Value value = {0};
    if (!input->message) return ose_fail(ctx, "missing input message");
    value.count = input->message->count + 100;
    return input->meta.has_app_id ? publish_out_with_id(ctx, &value, input->meta.app_id)
                                  : publish_out(ctx, &value);
}

const ComponentCallbacks component_callbacks = {.on_input = on_input};
