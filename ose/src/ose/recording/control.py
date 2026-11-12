"""Execution controls shared by simulated run and simulated/live replay."""
import asyncio
from decimal import Decimal, DecimalException, ROUND_HALF_UP
import json
import re
import sys

from ose.recording.command_input import CommandInput
from ose.recording.pacing import PlaybackClock
from ose.timing import MAX_NS


def control_help(*, replay=True, mode=None, microsteps=None):
    """Render the shared command reference for the available controls."""
    commands = [
        ("pause", "Pause progression."),
        ("resume", "Resume automatic progression."),
        ("step [N]", "Settle this instant, advance N future instants, then pause."),
        ("step DURATION", "Advance to an exclusive endpoint, e.g. step 100ms."),
        ("step-time DURATION", "Compatibility alias; bare numbers mean seconds."),
    ]
    if replay:
        commands.append(("step-messages [N]", "Inject N recorded messages, including timestamp ties."))
    if mode != "realtime" and microsteps is not False:
        commands.append(("microstep [N]", "Run up to N stages at this time (simulated --microsteps only)."))
    commands.extend([
        ("break-time TIME", "Arm a one-shot absolute-time breakpoint before events."),
        ("breakpoints", "List armed breakpoints."),
        ("clear-break-time TIME|all", "Remove one or all breakpoints."),
    ])
    if replay and mode != "simulated":
        commands.append(("speed N", "Live replay only: set speed; 0 means fastest."))
    commands.extend([
        ("wait DURATION", "Wait in wall time without resuming."),
        ("status", "Show position, state and counters."),
        ("help / ?", "Show this help."),
        ("stop / quit", "Stop the session."),
    ])
    return ("Execution commands:\n" + "\n".join(
        f"  {command:<28} {description}" for command, description in commands
    ) + "\n\nTime units: ns, us, ms, s, min. Counts default to 1."
      "\nTIME is measured from execution/replay position zero."
      "\nControls start paused; use resume or step to advance."
      "\nEOF or the end of a command script stops the session.\n")


def duration_ns(value, *, positive=True, unit_required=False):
    match = re.fullmatch(r'([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(ns|us|ms|s|min)?', value)
    if not match or (unit_required and match[2] is None):
        raise ValueError('duration requires finite nonnegative seconds or ns/us/ms/s/min; step durations require a unit')
    try:
        ns = Decimal(match[1]) * {'ns': 1, 'us': 1000, 'ms': 1000000,
                                  's': 1000000000, 'min': 60000000000}[match[2] or 's']
        if not ns.is_finite() or ns > MAX_NS or ns < 0 or (positive and ns == 0):
            raise ValueError('duration must be positive and within uint64 nanoseconds')
        return max(1 if ns > 0 else 0, int(ns.to_integral_value(rounding=ROUND_HALF_UP)))
    except DecimalException:
        raise ValueError('invalid duration') from None


def positive_count(value):
    if not re.fullmatch(r'[+]?\d+', value) or int(value) <= 0:
        raise ValueError('step count must be a positive integer')
    return int(value)


class ReplayControl:
    def __init__(self, mode, *, interactive=False, commands=None, speed=1, output=None,
                 replay=True, microsteps=False):
        if mode == 'realtime' and not replay:
            raise ValueError('execution controls require simulated mode for run')
        self.mode, self.interactive, self.commands = mode, interactive, commands
        self.replay, self.microsteps = replay, microsteps
        self.output = sys.stderr if output is None else output
        self.clock = PlaybackClock(speed)
        self.paused = interactive or commands is not None
        if self.paused:
            self.clock.pause()
            self.clock.anchor_ns = self.clock.last_ns = 0
        self.stopped = self.finished = False
        self.changed = asyncio.Event()
        self.quota = self.target = self.instant_quota = self.micro_quota = None
        self.operation = self.last_command = None
        self.pause_reason = 'initial' if self.paused else None
        self.breakpoints = set()
        self.last_breakpoint = None
        self.time_entered = False
        self.task = None
        self.snapshot = lambda: {}
        self.position = lambda: max(0, int(self.clock.anchor_ns if self.counting else self.clock.position()))
        self.busy = asyncio.Lock()
        self.error = None
        self.units = 0
        self.clock_observer = None

    def clock_changed(self):
        if self.clock_observer is not None:
            self.clock_observer()

    @property
    def counting(self):
        return any(value is not None for value in (self.quota, self.instant_quota, self.micro_quota))

    def seek(self, at):
        self.clock.anchor_ns = self.clock.last_ns = at
        self.clock.anchor_wall = self.clock.now()
        self.clock_changed()

    def status(self):
        return {**self.snapshot(), 'mode': self.mode, 'position_ns': self.position(),
                'paused': self.paused, 'stopped': self.stopped, 'pause_reason': self.pause_reason,
                'breakpoints_ns': sorted(self.breakpoints), 'last_breakpoint_ns': self.last_breakpoint,
                **({'speed': self.clock.speed} if self.mode == 'realtime' else {}),
                'last_command': self.last_command}

    def pause(self):
        if self.counting:
            self.clock.paused = True
        else:
            self.clock.pause()
        self.paused = True
        self.clock_changed()
        self.changed.set()

    def resume(self):
        self.clock.resume()
        self.paused = False
        self.pause_reason = None
        self.clock_changed()
        self.changed.set()

    def stop(self):
        self.stopped = True
        self.clock.stop()
        self.clock_changed()
        self.changed.set()

    def start(self):
        if self.interactive or self.commands is not None:
            self.task = asyncio.create_task(self.controls())

    async def ready(self):
        while self.paused and not self.stopped:
            self.changed.clear()
            await self.changed.wait()
        if self.error is not None:
            raise self.error
        return not self.stopped

    def boundary(self):
        candidates = list(self.breakpoints)
        if self.target is not None:
            candidates.append(self.target)
        return min(candidates) if candidates else None

    def complete(self, reason='step', **extra):
        if self.operation is None:
            return
        self.pause()
        self.pause_reason = reason
        future, before, command, count, deliveries, kind, ticks = self.operation
        snapshot = self.snapshot()
        self.last_command = dict(command=command, kind=kind, before_ns=before, after_ns=self.position(),
            messages=snapshot.get('published', 0) - count, completed_units=self.units,
            callbacks=snapshot.get('ticks', 0) - ticks, stop_reason=reason,
            delivery_results={key: value - deliveries.get(key, 0)
                for key, value in snapshot.get('delivery_results', {}).items()
                if value != deliveries.get(key, 0)}, **extra)
        self.operation = None
        self.quota = self.target = self.instant_quota = self.micro_quota = None
        if not future.done():
            future.set_result(self.last_command)

    def hit_breakpoint(self):
        at = min(self.breakpoints)
        self.breakpoints.remove(at)
        self.last_breakpoint = at
        self.time_entered = False
        self.clock.paused = True
        self.seek(at)
        if self.operation is not None:
            self.complete('breakpoint', breakpoint_ns=at)
        else:
            self.pause()
            self.pause_reason = 'breakpoint'
            print(json.dumps({**self.status(), 'event': 'breakpoint'}), file=self.output, flush=True)

    async def finish(self, reason):
        self.finished = True
        self.complete(reason)
        await asyncio.sleep(0)  # Acknowledge the last step before cancelling remaining commands.

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def execute(self, line):
        parts = line.split()
        if not parts or parts[0].startswith('#'):
            return True
        name = parts[0]
        if name == 'speed' and len(parts) == 2:
            if self.mode != 'realtime':
                raise ValueError('speed requires realtime mode')
            self.clock.set_speed(float(parts[1]))
            self.clock_changed()
            self.changed.set()
        elif parts == ['pause']:
            self.pause()
            self.pause_reason = 'pause'
            async with self.busy:
                pass
        elif parts == ['resume']:
            self.resume()
        elif name == 'wait' and len(parts) == 2:
            try:
                delay = duration_ns(parts[1], positive=False)
            except ValueError as error:
                raise ValueError(f'wait requires a finite nonnegative duration: {error}') from None
            await asyncio.sleep(delay / 1e9)
        elif name == 'break-time' and len(parts) == 2:
            at = duration_ns(parts[1], positive=False)
            async with self.busy:
                now = self.position()
                if at < now or (at == now and self.time_entered):
                    raise ValueError('breakpoint time has already passed or its events have started')
                self.breakpoints.add(at)
                self.changed.set()
        elif name == 'clear-break-time' and len(parts) == 2:
            if parts[1] == 'all':
                self.breakpoints.clear()
            else:
                at = duration_ns(parts[1], positive=False)
                if at not in self.breakpoints:
                    raise ValueError('no breakpoint at that time')
                self.breakpoints.remove(at)
            self.changed.set()
        elif name in ('step', 'step-time', 'step-messages', 'microstep'):
            if name == 'step-time' and len(parts) == 2:
                kind, value = 'time', duration_ns(parts[1])
            elif name == 'step' and len(parts) in (1, 2):
                arg = parts[1] if len(parts) == 2 else '1'
                if re.fullmatch(r'[+]?\d+', arg):
                    kind, value = 'instant', positive_count(arg)
                else:
                    kind, value = 'time', duration_ns(arg, unit_required=True)
            elif name in ('step-messages', 'microstep') and len(parts) in (1, 2):
                kind = 'message' if name == 'step-messages' else 'microstep'
                value = positive_count(parts[1] if len(parts) == 2 else '1')
            else:
                raise ValueError('expected step [N|DURATION], step-messages [N] or microstep [N]')
            if kind == 'message' and not self.replay:
                raise ValueError('step-messages requires replay')
            if kind == 'microstep' and (self.mode != 'simulated' or not self.microsteps):
                raise ValueError('microstep requires simulated mode with --microsteps')
            self.pause()
            async with self.busy:
                before = self.position()
                if kind == 'time' and before + value > MAX_NS:
                    raise ValueError('step target exceeds uint64 nanoseconds')
                future = asyncio.get_running_loop().create_future()
                snapshot = self.snapshot()
                self.operation = (future, before, line.strip(), snapshot.get('published', 0),
                                  dict(snapshot.get('delivery_results', {})), kind, snapshot.get('ticks', 0))
                self.units = 0
                self.quota = value if kind == 'message' else None
                self.instant_quota = value if kind == 'instant' else None
                self.micro_quota = value if kind == 'microstep' else None
                self.target = before + value if kind == 'time' else None
                self.resume()
            await future
        elif parts in (['stop'], ['quit']):
            self.stop()
        elif parts in (['help'], ['?']):
            print(control_help(replay=self.replay, mode=self.mode, microsteps=self.microsteps),
                  file=self.output, flush=True)
        elif parts not in (['status'], ['breakpoints']):
            raise ValueError('expected an execution control command; use help')
        print(json.dumps(self.status()), file=self.output, flush=True)
        return not (self.stopped or self.finished)

    async def controls(self):
        source = CommandInput(output=self.output) if self.commands is None else None
        try:
            if self.commands is not None:
                for line in self.commands:
                    if not await self.execute(line):
                        break
                self.stop()
                return
            print(control_help(replay=self.replay, mode=self.mode, microsteps=self.microsteps),
                  file=self.output, flush=True)
            while not self.stopped:
                line = await source.readline('replay> ' if self.replay else 'simulation> ')
                if not line:
                    self.stop()
                    break
                try:
                    if not await self.execute(line):
                        break
                except ValueError as error:
                    print(f'execution: {error}', file=self.output, flush=True)
        except Exception as error:
            self.error = error
            self.stop()
        finally:
            if source is not None:
                source.close()

    async def live_wait(self, at, *, terminal=False):
        """Wait for publication, a time limit or an armed breakpoint."""
        while not self.stopped:
            if not await self.ready():
                break
            self.changed.clear()
            limit = self.boundary()
            boundary = min(at, limit) if limit is not None else at
            delay = ((boundary - self.clock.position()) / (self.clock.speed * 1e9)
                     if self.clock.speed and not self.counting else 0)
            if delay > 0:
                try:
                    await asyncio.wait_for(self.changed.wait(), delay)
                except asyncio.TimeoutError:
                    pass
                continue
            # Global end/horizon takes precedence over a breakpoint at that end.
            if terminal and boundary == at:
                self.clock.paused = True
                self.seek(at)
                return 0.0
            if self.breakpoints and boundary == min(self.breakpoints):
                self.hit_breakpoint()
                continue
            if self.target is not None and boundary == self.target:
                self.clock.paused = True
                self.seek(self.target)
                self.complete('time')
                continue
            if self.counting:
                if at > self.position():
                    self.time_entered = False
                self.seek(max(self.position(), at))
            else:
                self.clock.last_ns = at
                if self.clock.speed == 0:
                    self.clock_changed()
            return max(0, -delay)
        if self.error is not None:
            raise self.error
        return None
