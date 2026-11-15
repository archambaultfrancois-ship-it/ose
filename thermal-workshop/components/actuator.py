from messages import Actuation, Stage, Diagnostic
from common import start, observe, clamp


def on_start(ctx):
    start(ctx, 'power', 'stages')
    ctx.power = .25


def on_input(ctx, input):
    command = input.message
    previous = ctx.power
    ctx.power = clamp(command.power, max(0., previous - .08), min(1., previous + .08))
    count = command.ramp.stages if command.ramp else 1
    stages = [Stage(index=n, power=previous + (ctx.power - previous) * (n + 1) / count,
                    note='last stage' if n == count - 1 else None) for n in range(count)]
    ctx.publish_applied(Actuation(header=command.header, power=ctx.power, stages=stages,
        diagnostic=Diagnostic(code=2, text='Power slew limited', hints=['normal protection'])
            if abs(ctx.power - command.power) > .001 else None), id=input.app_id)
    observe(ctx, power=ctx.power, stages=count)
