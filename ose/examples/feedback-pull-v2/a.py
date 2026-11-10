"""Timer-driven component with a passive feedback inbox."""
from value import Value


def on_start(ctx):
    ctx.declare_measurement("feedback.count")


def on_clock(ctx, input):
    previous = ctx.inbox("feedback").pull()
    if previous is not None:
        ctx.measure("feedback.count", previous.message.count)
    ctx.publish_out(Value(count=ctx.activation_index), id=ctx.activation_index)
    if ctx.activation_index == 3:
        ctx.disarm("clock")
