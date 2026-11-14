"""Persistent terminal adapter for the simulation command service.

Input/history belong to the server. Session controllers never own stdin. Scripts
wait for each operation; interactive advances report completion asynchronously.
"""
import asyncio
import json
import os
import re
import stat
import sys
import uuid

from ose.recording.command_input import CommandInput
from ose.recording.control import control_help, duration_ns, positive_count
from ose.timing import MAX_NS


class QuietOutput:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def parse_command(line):
    """Keep simulation's existing command spelling and duration conversion."""
    parts = line.split()
    if not parts or parts[0].startswith('#'):
        return None
    name = parts[0]
    if name == 'break-time' and len(parts) == 3 and parts[2] == '--persistent':
        return {'action': 'break_add', 'time_ns': str(duration_ns(parts[1], positive=False)), 'persistent': True}
    if len(parts) == 1:
        if name in ('status', 'breakpoints', 'help', '?', 'pause', 'resume', 'reset', 'stop', 'quit'):
            return {'action': {'?': 'help', 'quit': 'stop'}.get(name, name)}
        if name in ('step', 'microstep'):
            return {'action': 'step_instants' if name == 'step' else 'step_microsteps', 'count': 1}
    if name in ('step', 'step-time', 'microstep') and len(parts) == 2:
        if name == 'microstep' or name == 'step' and re.fullmatch(r'[+]?\d+', parts[1]):
            return {'action': 'step_microsteps' if name == 'microstep' else 'step_instants',
                    'count': positive_count(parts[1])}
        return {'action': 'step_duration', 'time_ns': str(duration_ns(parts[1], unit_required=name == 'step'))}
    if name in ('break-time', 'clear-break-time', 'run-until', 'wait') and len(parts) == 2:
        if name == 'clear-break-time' and parts[1] == 'all':
            return {'action': 'break_clear'}
        return {'action': {'break-time': 'break_add', 'clear-break-time': 'break_remove',
                           'run-until': 'run_until', 'wait': 'wait'}[name],
                'time_ns': str(duration_ns(parts[1], positive=False))}
    if name == 'component' and len(parts) >= 2 and parts[1] in ('status', 'enable', 'disable'):
        return {'action': 'component_status' if parts[1] == 'status' else 'component_set',
                'components': parts[2:], **({'enabled': parts[1] == 'enable'} if parts[1] != 'status' else {})}
    if name == 'step-messages':
        raise ValueError('step-messages requires replay')
    if name == 'speed':
        raise ValueError('speed requires realtime mode')
    raise ValueError('expected a simulation command; use help')


def help_text(microsteps):
    text = control_help(replay=False, mode='simulated', microsteps=microsteps)
    return text.replace('  stop / quit',
        '  break-time TIME --persistent  Re-arm this breakpoint after each reset.\n'
        '  reset                        Recreate the initial session, paused.\n'
        '  run-until TIME               Advance to an exclusive absolute target.\n'
        '  stop / quit').replace('EOF or the end of a command script stops the session.',
        'EOF detaches the terminal. A command script ends the session.\n'
        'The prompt survives session completion and reset. Ctrl+C closes the server.')


class SimulationConsole:
    def __init__(self, server, *, commands=None, output=None, source=None):
        self.server, self.commands = server, commands
        self.output = output if output is not None else sys.stderr
        self.source = source
        self.tasks = set()
        self.error = None

    def report(self, value):
        message = value if isinstance(value, str) else json.dumps(value, separators=(',', ':'))
        if self.source is not None and hasattr(self.source, 'display'):
            self.source.display(message)
        else:
            print(message, file=self.output, flush=True)

    async def execute(self, line, *, wait=True):
        command = parse_command(line)
        if command is None:
            return None
        action = command['action']
        if action == 'help':
            self.report(help_text(self.server.execution()['microsteps']))
            return None
        if action in ('status', 'breakpoints'):
            result, _ = await self.server.request(None, {'op': 'status'}, b'')
            self.report(result)
            return result
        if action == 'component_status':
            result, _ = await self.server.request(None, {'op': 'component_status', 'components': command['components']}, b'')
            self.report({'components': result})
            return result
        if action == 'wait':
            await asyncio.sleep(int(command['time_ns']) / 1e9)
            return None
        if action == 'stop' and self.server.state in ('finished', 'failed'):
            return await self.execute('status')
        if action == 'step_duration':
            # CLI duration steps historically clip to the simulation horizon.
            # Web absolute/relative targets retain their stricter validation.
            execution = self.server.execution()
            now = int(execution['position_ns'])
            duration = int(command['time_ns'])
            if now + duration > MAX_NS:
                raise ValueError('step target exceeds uint64 nanoseconds')
            horizon = int(execution['horizon_ns']) if execution['horizon_ns'] is not None else None
            if horizon is not None:
                command['time_ns'] = str(min(duration, max(0, horizon - now)))
        operation = await self.server.command({'epoch': self.server.epoch,
            'command_id': uuid.uuid4().hex, **command})
        if wait:
            return await self._complete(operation)
        self.report({'operation': operation})
        task = asyncio.create_task(self._complete(operation))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return operation

    async def _complete(self, operation):
        result = await self.server.wait_operation(operation['id'])
        self.report({'operation': result, 'execution': self.server.execution()})
        if result['status'] == 'failed':
            if self.commands is not None:
                raise ValueError(result.get('error') or 'simulation command failed')
        return result

    async def _monitor(self):
        previous = None
        while True:
            state = self.server.execution()
            marker = (state['epoch'], state['state'], state['pause_reason'])
            if marker != previous and (state['state'] in ('finished', 'failed') or
                                      state['pause_reason'] == 'breakpoint'):
                self.report({'execution': state})
            previous = marker
            await asyncio.sleep(.1)

    async def run(self):
        await self.server.ready.wait()
        try:
            if self.commands is not None:
                for line in self.commands:
                    await self.execute(line)
                    if self.server.state in ('finished', 'failed'):
                        break
                if self.server.state == 'ready':
                    await self.execute('stop')
                return
            if self.source is None:
                mode = os.fstat(sys.stdin.fileno()).st_mode
                if stat.S_ISCHR(mode) and not sys.stdin.isatty():
                    return  # Detached web server with /dev/null as stdin.
                self.source = CommandInput(output=self.output)
                interactive_input = sys.stdin.isatty()
            else:
                interactive_input = True
            self.report(help_text(self.server.execution()['microsteps']))
            monitor = asyncio.create_task(self._monitor())
            self.tasks.add(monitor)
            while True:
                line = await self.source.readline('simulation> ')
                if not line:
                    return
                try:
                    command = parse_command(line)
                    advance = command and (command['action'].startswith('step_') or command['action'] == 'run_until')
                    await self.execute(line, wait=not (interactive_input and advance))
                except ValueError as error:
                    self.report('execution: ' + str(error))
        except Exception as error:
            self.error = error
            if self.commands is not None:
                self.report('execution: ' + str(error))
                self.server.control.stop()
            else:
                self.report('simulation terminal detached: ' + str(error))
        finally:
            await self.close()

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.source is not None:
            self.source.close()
