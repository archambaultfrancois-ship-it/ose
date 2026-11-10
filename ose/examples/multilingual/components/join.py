from measurement import Measurement

BRANCHES = frozenset(("c_worker", "rust_worker", "java_worker", "ada_worker"))


def on_start(ctx):
    ctx.pending = {}


def receive(ctx, input):
    sequence = input.message.sequence
    if sequence not in ctx.pending:
        if len(ctx.pending) >= 1024:
            raise RuntimeError("join exceeds 1024 incomplete sequences")
        ctx.pending[sequence] = {}
    group = ctx.pending[sequence]
    if input.port in group:
        raise ValueError("duplicate branch for one sequence")
    group[input.port] = input.message
    if group.keys() == BRANCHES:
        ctx.publish_value(Measurement(sequence=sequence,
            value=sum(group[port].value for port in sorted(BRANCHES))), id=sequence)
        del ctx.pending[sequence]


def on_c_worker(ctx, input):
    receive(ctx, input)


def on_rust_worker(ctx, input):
    receive(ctx, input)


def on_java_worker(ctx, input):
    receive(ctx, input)


def on_ada_worker(ctx, input):
    receive(ctx, input)
