"""Print observations to stderr; delivery and the example do not persist messages."""
import json
import sys
from measurement import Measurement


def observe(ctx, message: Measurement):
    print(json.dumps({"observer": ctx.name, "sequence": message.sequence, "value": message.value}),
          file=sys.stderr, flush=True)


def on_input(ctx, input):
    observe(ctx, input.message)
