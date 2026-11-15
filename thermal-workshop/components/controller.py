from messages import Command, Ramp, Tag
from common import start, observe, clamp


def on_start(ctx):
    start(ctx, 'target', 'error', 'power')
    ctx.offset = 0.0


def on_input(ctx, input):
    analysis, settings = input.message, ctx.parameters_cell()
    feedback = ctx.inbox('policy').pull()
    if feedback is not None:
        for setpoint in feedback.message.setpoints:
            if setpoint.cell == ctx.cell:
                ctx.offset = clamp(setpoint.target - settings.target, -8., 8.)
    target = clamp(settings.target + ctx.offset, 25., 100.)
    error = target - analysis.temperature
    power = clamp((target - settings.ambient) / 90 + clamp(settings.gain, 0., .1) * error, 0., 1.)
    command = Command(header=analysis.header, power=power, target=target,
        ramp=Ramp(slope=.1, stages=2 + analysis.header.sequence % 5) if abs(error) > 3 else None,
        reasons=[Tag(key='regulation', value='feedforward+P')] +
            ([Tag(key='supervision', value='energy balance')] if ctx.offset else []))
    ctx.publish_command(command, id=input.app_id)
    observe(ctx, target=target, error=error, power=power)
