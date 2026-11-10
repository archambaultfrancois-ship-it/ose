from measurement import Measurement


def on_clock(ctx, input):
    ctx.publish_value(Measurement(sequence=ctx.tick_index, value=ctx.now()), id=ctx.tick_index)
