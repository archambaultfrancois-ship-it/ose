from messages import Policy, Setpoint
from common import start, observe, clamp


def on_start(ctx):
    start(ctx, 'mean_temperature', 'correction', 'setpoints')


def on_input(ctx, input):
    fleet = input.message
    mean = sum(cell.temperature for cell in fleet.cells) / max(1, len(fleet.cells))
    setpoints = []
    for cell in fleet.cells:
        settings = getattr(ctx, f'parameters_cell_{cell.header.cell}')()
        correction = clamp((mean - cell.temperature) * .15, -5., 5.)
        setpoints.append(Setpoint(cell=cell.header.cell, target=settings.target + correction,
            reason='Reduce imbalance' if abs(correction) > .5 else None))
    ctx.publish_policy(Policy(header=fleet.header, setpoints=setpoints,
        note='Alarm-aware balancing' if fleet.alarms else None), id=input.app_id)
    observe(ctx, mean_temperature=mean, correction=max((abs(point.target - getattr(ctx, f'parameters_cell_{point.cell}')().target) for point in setpoints), default=0), setpoints=len(setpoints))
