"""Respond once per received message, retaining its correlation ID."""
from value import Value


def on_input(ctx, input):
    ctx.publish_out(Value(count=input.message.count + 1), id=input.app_id)
