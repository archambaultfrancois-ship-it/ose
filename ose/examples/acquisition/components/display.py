"""Sample the latest observation on an independent display timer."""
from observer import observe


def on_clock(ctx, input):
    latest = ctx.inbox("input").get()
    if latest is not None:
        observe(ctx, latest.message)
