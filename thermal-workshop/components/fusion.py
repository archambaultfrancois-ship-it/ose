from messages import Header, FleetReport, Alarm, Feature
from common import start, observe


def on_start(ctx):
    start(ctx, 'cells', 'mean_temperature', 'alarms')
    ctx.latest, ctx.history, ctx.sequence = {}, [], 0


def on_input(ctx, input):
    analysis = input.message
    ctx.latest[analysis.header.cell] = analysis


on_cell_1 = on_cell_2 = on_cell_3 = on_cell_4 = on_input


def on_clock(ctx, input):
    if not ctx.latest:
        return
    ctx.sequence += 1
    cells = [ctx.latest[cell] for cell in sorted(ctx.latest)]
    mean = sum(cell.temperature for cell in cells) / len(cells)
    ctx.history = (ctx.history + [mean])[-64:]
    alarms = [Alarm(cell=cell.header.cell, severity=1,
        evidence=[Feature(name='temperature', value=cell.temperature, unit='C')])
        for cell in cells if cell.temperature > 75 or cell.diagnostic is not None]
    ctx.publish_report(FleetReport(header=Header(cell=0, sequence=ctx.sequence, time_ns=ctx.time_ns),
        cells=cells, alarms=alarms, history=ctx.history,
        note='Partial inventory' if len(cells) < 4 else None), id=ctx.sequence)
    observe(ctx, cells=len(cells), mean_temperature=mean, alarms=len(alarms))
