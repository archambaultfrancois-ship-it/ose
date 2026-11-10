"""Push response preserving the sender's application correlation ID."""
from value import Value


def on_input(ctx, input):
    ctx.publish_out(Value(count=input.message.count + 100), id=input.app_id)
