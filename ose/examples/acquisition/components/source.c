#include "component.h"

static uint64_t counter;

static int on_clock(OseContext *ctx, const Input_clock *input) {
    (void)input;
    Measurement message = {0};
    message.sequence = ++counter;
    message.value = (double)counter;
    return publish_measure_with_id(ctx, &message, message.sequence);
}

const ComponentCallbacks component_callbacks = { .on_clock = on_clock };
