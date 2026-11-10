"""Correlate two Push streams; consume an optional Pull offset per result."""
from measurement import Measurement


def on_start(ctx):
    ctx.pending = {}


def receive(ctx, input):
    sequence = input.message.sequence
    if sequence not in ctx.pending:
        if len(ctx.pending) >= 1024:
            raise RuntimeError("join exceeds 1024 incomplete sequences")
        ctx.pending[sequence] = {}
    pair = ctx.pending[sequence]
    if input.port in pair:
        raise ValueError("duplicate branch for one sequence")
    pair[input.port] = input.message
    if len(pair) != 2:
        return
    offset = ctx.inbox("offset").pull()
    ctx.publish_result(Measurement(sequence=sequence,
        value=pair["left"].value + pair["right"].value + (offset.message.value if offset is not None else 0.0)),
        id=sequence)
    del ctx.pending[sequence]


def on_left(ctx, input):
    receive(ctx, input)


def on_right(ctx, input):
    receive(ctx, input)
