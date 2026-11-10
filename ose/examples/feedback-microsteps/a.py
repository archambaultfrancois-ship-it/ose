"""An explicit timer starts a finite activating feedback loop."""
from value import Value


def on_clock(ctx, input):
    ctx.publish_out(Value(count=0), id=0)


def on_feedback(ctx, input):
    if input.message.count < 6:
        ctx.publish_out(Value(count=input.message.count + 1), id=input.app_id)
