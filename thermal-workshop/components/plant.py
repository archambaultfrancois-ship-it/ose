import math
from messages import Header, Tag, Diagnostic, Channel, SensorFrame
from common import start, observe, clamp, samples


def on_start(ctx):
    start(ctx, 'temperature', 'power', 'samples', 'channels')
    ctx.temperature, ctx.power, ctx.sequence, ctx.last_time = 22.0, .25, 0, 0.0


def on_clock(ctx, input):
    settings, workload = ctx.parameters_cell(), ctx.parameters_workload()
    feedback = ctx.inbox('feedback').pull()
    if feedback is not None:
        ctx.power = feedback.message.power
    dt = max(0, ctx.now() - ctx.last_time)
    ctx.last_time = ctx.now()
    equilibrium = clamp(settings.ambient, -20., 60.) + ctx.power * 90
    ctx.temperature += (equilibrium - ctx.temperature) * (1 - math.exp(-dt / 2.5))
    ctx.temperature += .015 * math.sin(ctx.now() + ctx.cell)
    minimum = clamp(workload.min_samples, 1, 8192)
    maximum = clamp(workload.max_samples, minimum, 8192)
    for _ in range(clamp(workload.burst, 1, 16)):
        ctx.sequence += 1
        sequence = ctx.sequence
        count = minimum + (sequence * 127 + ctx.cell * 31) % (maximum - minimum + 1)
        channels = [Channel(name=f'thermocouple-{j}',
            samples=samples(ctx.temperature, sequence, j, max(1, count // (j + 1))),
            tags=[Tag(key='unit', value='C'), Tag(key='zone', value=f'cell-{ctx.cell}/probe-{j}')])
            for j in range(1 + sequence % 4)]
        frame = SensorFrame(header=Header(cell=ctx.cell, sequence=sequence, time_ns=ctx.time_ns),
            channels=channels, tags=[Tag(key='batch', value=str(sequence // 20))] +
                ([Tag(key='label', value=workload.label)] if workload.label else []),
            diagnostic=Diagnostic(code=1, text='Synthetic calibration warning', hints=['inspect probe', 'check reference']) if sequence % 11 == 0 else None,
            note=('Synthetic run ' + str(sequence) + ' ' + 'thermal ' * (sequence % 12)) if sequence % 3 == 0 else None)
        ctx.publish_frame(frame, id=sequence)
        observe(ctx, temperature=ctx.temperature, power=ctx.power,
            samples=sum(len(channel.samples) for channel in channels), channels=len(channels))
