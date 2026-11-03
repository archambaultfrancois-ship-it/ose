from alarm import Alarm
from policy import classify


def on_start(ctx):
    ctx.alarm_states = {}
    ctx.log('INFO', 'Alarm policy started', 'lifecycle')


def on_stop(ctx):
    ctx.log('INFO', 'Alarm policy stopped', 'lifecycle')


def process(ctx, message):
    settings = getattr(ctx, f"parameters_detection_{message.machine_id}")()
    threshold = settings.rms_threshold
    state = classify(message.rms, threshold)
    ctx.publish_event(Alarm(machine_id=message.machine_id, sequence=message.sequence,
        time_ns=message.time_ns, rms=message.rms, peak=message.peak,
        state=state))
    previous = ctx.alarm_states.get(message.machine_id)
    changed = previous != state
    if changed or message.sequence % 10 == 0:
        level = ('WARNING' if state else 'INFO') if changed else 'DEBUG'
        ctx.logf(level, 'Detection: machine=%d sequence=%d state=%s rms=%.3f peak=%.3f threshold=%.3f',
                 message.machine_id, message.sequence, 'ALARM' if state else 'NORMAL',
                 message.rms, message.peak, threshold, logger='detection')
    ctx.alarm_states[message.machine_id] = state


def on_machine_1(ctx, message):
    process(ctx, message)


def on_machine_2(ctx, message):
    process(ctx, message)
