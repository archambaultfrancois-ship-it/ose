#include "component.h"
static int on_input(OseContext *ctx, const Input_input *input) {
    Measurement result = *input->message;
    result.value += 1.0;
    return input->meta.has_app_id ? publish_value_with_id(ctx, &result, input->meta.app_id) : publish_value(ctx, &result);
}
const ComponentCallbacks component_callbacks = { .on_input = on_input };
