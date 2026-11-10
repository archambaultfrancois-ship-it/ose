#include "component.h"

static int on_start(OseContext *ctx) {
    return ose_declare_measurement(ctx, "feedback.count");
}

static int on_clock(OseContext *ctx, const Input_clock *input) {
    Input_feedback previous;
    Value value = {0};
    (void)input;
    if (!pull_feedback(ctx, NULL, &previous)) return 0;
    if (previous.message && !ose_measure(ctx, "feedback.count", previous.message->count)) return 0;
    value.count = (uint32_t)ctx->tick_index;
    if (!publish_out_with_id(ctx, &value, ctx->tick_index)) return 0;
    return ctx->tick_index != 3 || ose_disarm(ctx, "clock");
}

const ComponentCallbacks component_callbacks = {.on_start = on_start, .on_clock = on_clock};
