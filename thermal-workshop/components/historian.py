from common import start, observe


def on_start(ctx):
    start(ctx, 'cells', 'history_points')
    ctx.latest = None


def on_clock(ctx, input):
    report = ctx.inbox('input').pull()
    if report is not None:
        ctx.latest = report.message
    if ctx.latest is not None:
        ctx.publish_archive(ctx.latest, id=ctx.latest.header.sequence)
        observe(ctx, cells=len(ctx.latest.cells), history_points=len(ctx.latest.history))
