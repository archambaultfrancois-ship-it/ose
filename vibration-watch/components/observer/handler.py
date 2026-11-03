import json
import sys


def on_start(ctx):
    ctx.log('INFO', 'Observer started', 'lifecycle')


def on_stop(ctx):
    ctx.log('INFO', 'Observer stopped', 'lifecycle')


def on_input(ctx, message):
    settings = ctx.parameters_display()
    if settings.machine_ids and message.machine_id not in settings.machine_ids:
        return
    output_format = settings.output.format if settings.output is not None else None
    event = {"event": "vibration", "machine_id": message.machine_id}
    if output_format is None or output_format.include_sequence:
        event["sequence"] = message.sequence
    if output_format is None or output_format.include_time_ns:
        event["time_ns"] = message.time_ns
    event.update(rms=message.rms, peak=message.peak, state=message.state)
    if settings.label is not None:
        event["label"] = settings.label
    print(json.dumps(event), file=sys.stderr, flush=True)
    if message.sequence == 1 or message.sequence % 10 == 0:
        ctx.logf('DEBUG', 'Displayed vibration: machine=%d sequence=%d rms=%.3f peak=%.3f state=%d',
                 message.machine_id, message.sequence, message.rms, message.peak, message.state,
                 logger='display')
