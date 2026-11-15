"""Bounded, deterministic workload shared by the workshop handlers."""
import math
try:
    from _thermal_native import samples, burn as _burn
except ImportError:
    _burn = None

    def samples(temperature, sequence, channel, count):
        return [temperature + .2 * math.sin(k * .11 + sequence + channel) for k in range(count)]


def clamp(value, low, high):
    return min(high, max(low, value))


def start(ctx, *metrics):
    ctx.cell = int(ctx.parameters.get('cell', 0))
    ctx.received = 0
    for metric in ('messages', *metrics):
        ctx.declare_measurement(metric)
    ctx.log('INFO', f'{ctx.name} ready', 'lifecycle')


def observe(ctx, **metrics):
    ctx.received += 1
    ctx.measure('messages', float(ctx.received))
    for name, value in metrics.items():
        ctx.measure(name, float(value))
    every = clamp(ctx.parameters_workload().log_every, 1, 10000)
    if ctx.received % every == 0:
        ctx.log('INFO', f'{ctx.name}: messages={ctx.received} {metrics}', 'workload')


def burn(iterations, seed):
    if _burn is not None:
        return _burn(clamp(iterations, 0, 200000), seed)
    value = seed
    for n in range(clamp(iterations, 0, 200000)):
        value = math.sin(value + n * .001) * .5
    return value
