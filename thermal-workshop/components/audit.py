from common import start, observe


def on_start(ctx):
    start(ctx, 'reports', 'policies', 'last_sequence')
    ctx.reports = ctx.policies = 0


def on_report(ctx, input):
    ctx.reports += 1
    observe(ctx, reports=ctx.reports, policies=ctx.policies, last_sequence=input.message.header.sequence)


def on_policy(ctx, input):
    ctx.policies += 1
    observe(ctx, reports=ctx.reports, policies=ctx.policies, last_sequence=input.message.header.sequence)
