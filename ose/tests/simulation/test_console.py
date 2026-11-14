"""CLI/web interoperability on one persistent server and unchanged replay controls."""
import asyncio
import io
import json
import os
import unittest

from ose.execution.simulation import Simulation
from ose.execution.simulation.console import SimulationConsole, parse_command
from ose.transport.tcp import RemoteError
from tests.simulation import test_server as fixtures


class QueueInput:
    def __init__(self):
        self.lines = asyncio.Queue()
        self.history = []
        self.reads = 0
        self.closed = False

    async def readline(self, prompt):
        self.reads += 1
        line = await self.lines.get()
        if line:
            self.history.append(line.strip())
        return line

    def feed(self, line):
        self.lines.put_nowait(line + '\n' if line else '')

    def close(self):
        self.closed = True


class CommandParsingTests(unittest.TestCase):
    def test_aliases_counts_units_comments_and_invalid_replay_commands(self):
        self.assertIsNone(parse_command('# comment'))
        self.assertEqual(parse_command('step +2'), {'action': 'step_instants', 'count': 2})
        self.assertEqual(parse_command('step 0.000000001s'), {'action': 'step_duration', 'time_ns': '1'})
        self.assertEqual(parse_command('step-time 0.1'), {'action': 'step_duration', 'time_ns': '100000000'})
        self.assertEqual(parse_command('break-time 0ns'), {'action': 'break_add', 'time_ns': '0'})
        self.assertEqual(parse_command('clear-break-time all'), {'action': 'break_clear'})
        self.assertEqual(parse_command('component disable a b'), {'action': 'component_set', 'components': ['a', 'b'], 'enabled': False})
        for line in ('step 0', 'step 0.1', 'step 1 2', 'step-time nan', 'microstep -1', 'step-messages', 'speed 2'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                parse_command(line)


@unittest.skipUnless(os.environ.get('SDL_ROOT'), 'set SDL_ROOT')
class SimulationConsoleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.SimulationServerTests.asyncSetUp
    submit = fixtures.SimulationServerTests.submit
    wait = fixtures.SimulationServerTests.wait

    async def until(self, predicate):
        for _ in range(500):
            if predicate():
                return
            await asyncio.sleep(.01)
        self.fail('console/session did not reach the expected state')

    async def terminal(self):
        source, output = QueueInput(), io.StringIO()
        console = SimulationConsole(self.server, source=source, output=output)
        task = asyncio.create_task(console.run())
        async def close():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.addAsyncCleanup(close)
        await self.until(lambda: source.reads == 1)
        return console, source, output, task

    async def test_prompt_and_history_survive_completion_web_reset_and_cli_reset(self):
        console, source, output, task = await self.terminal()
        source.feed('step 1s')  # Existing CLI steps clip at the horizon.
        await self.until(lambda: self.server.state == 'finished')
        self.assertFalse(task.done())
        self.assertFalse(source.closed)
        original_epoch = self.server.epoch
        await self.wait(await self.submit('reset'))
        self.assertNotEqual(self.server.epoch, original_epoch)
        self.assertEqual(self.server.execution()['state'], 'paused')
        source.feed('step 10ms')
        await self.until(lambda: self.server.engine.clock.time_ns == 10000000 and self.server.active is None)
        epoch = self.server.epoch
        source.feed('reset')
        await self.until(lambda: self.server.epoch != epoch and self.server.state == 'ready' and self.server.active is None)
        source.feed('status')
        await self.until(lambda: len(source.history) == 4)
        self.assertEqual(source.history, ['step 1s', 'step 10ms', 'reset', 'status'])
        self.assertFalse(task.done())
        self.assertIn(self.server.epoch, output.getvalue())
        self.assertIsNone(console.error)

    async def test_terminal_can_read_status_and_pause_while_its_advance_is_running(self):
        console, source, output, task = await self.terminal()
        entered, release = asyncio.Event(), asyncio.Event()
        calculate = self.server.engine._calculate
        async def slow(*args):
            entered.set()
            await release.wait()
            return await calculate(*args)
        self.server.engine._calculate = slow
        source.feed('step 90ms')
        await asyncio.wait_for(entered.wait(), 3)
        advancing = self.server.active
        with self.assertRaises(RemoteError):
            await self.submit('step_instants', count=1)
        source.feed('status')
        source.feed('pause')
        await self.until(lambda: any(op['action'] == 'pause' for op in self.server.operations.values()))
        self.assertIn('status', source.history)
        release.set()
        await self.until(lambda: self.server.active is None and self.server.control.paused)
        operation = self.server.operations[advancing]
        self.assertEqual(operation['status'], 'interrupted')
        self.assertEqual(operation['stop_reason'], 'interrupted')
        self.assertFalse(task.done())
        self.assertIn('interrupted', output.getvalue())

    async def test_breakpoints_components_and_stop_are_shared_with_network_clients(self):
        console, _, output, task = await self.terminal()
        await console.execute('break-time 20ms')
        status, _ = await self.client.request('status')
        self.assertEqual(status['execution']['breakpoints_ns'], ['20000000'])
        await console.execute('resume')
        await self.until(lambda: self.server.control.pause_reason == 'breakpoint')
        self.assertEqual(self.server.control.position(), 20000000)
        await self.until(lambda: '"pause_reason":"breakpoint"' in output.getvalue())
        await self.wait(await self.submit('break_add', time_ns='30000000'))
        await console.execute('breakpoints')
        self.assertIn('30000000', output.getvalue())
        await console.execute('clear-break-time all')
        self.assertEqual(self.server.control.breakpoints, set())
        await console.execute('component disable source')
        result, _ = await self.client.request('component_status')
        self.assertFalse(result['source']['active'])
        await console.execute('component enable source')
        await console.execute('component status source')
        self.assertIn('"source":{"active":true}', output.getvalue())
        with self.assertRaises(ValueError):
            await console.execute('component disable unknown')
        await console.execute('stop')
        self.assertEqual(self.server.state, 'finished')
        self.assertFalse(task.done())
        await console.execute('reset')
        self.assertEqual(self.server.control.breakpoints, set())

    async def test_errors_recover_and_eof_detaches_without_stopping_the_session(self):
        console, source, output, task = await self.terminal()
        source.feed('step-messages 1')
        source.feed('status')
        source.feed('')
        await asyncio.wait_for(task, 3)
        self.assertTrue(source.closed)
        self.assertIn('requires replay', output.getvalue())
        self.assertIsNone(console.error)
        self.assertFalse(self.server.task.done())
        self.assertEqual(self.server.execution()['state'], 'paused')
        await self.wait(await self.submit('step_duration', time_ns='10000000'))

    async def test_script_waits_for_each_step_and_preserves_the_continuous_trace(self):
        output = io.StringIO()
        console = SimulationConsole(self.server, commands=['step 20ms', 'step-time 0.01', 'step 1s'], output=output)
        await asyncio.wait_for(console.run(), 5)
        await self.server.task
        self.assertIsNone(console.error)
        operations = [json.loads(line)['operation'] for line in output.getvalue().splitlines()]
        self.assertEqual([o['position_ns'] for o in operations], ['20000000', '30000000', '100000000'])
        summary = await Simulation(self.plan, .1).execute()
        self.assertEqual(self.server.engine.summary['trace_sha256'], summary['trace_sha256'])

    async def test_script_errors_end_the_session_without_hanging(self):
        console = SimulationConsole(self.server, commands=['step 0'], output=io.StringIO())
        await asyncio.wait_for(console.run(), 3)
        await asyncio.wait_for(self.server.task, 3)
        self.assertIsInstance(console.error, ValueError)
        self.assertEqual(self.server.engine.reason, 'stopped')

    async def test_real_terminal_survives_completion_reset_breakpoint_and_eof(self):
        import signal
        import sys
        from ose.transport.tcp import Client
        build = self.root / 'terminal-build'
        build.mkdir()
        plan_path = self.root / 'terminal-plan.json'
        plan_path.write_text(json.dumps(self.plan | {'build_dir': str(build)}))
        master, slave = os.openpty()
        process = None
        client = None
        readers = []
        output = bytearray()
        async def drain(stream):
            while chunk := await stream.read(4096):
                output.extend(chunk)
        try:
            process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'ose.cli', 'run',
                '--mode', 'simulated', '--plan', str(plan_path), '--duration', '.1', '--interactive',
                stdin=slave, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True)
            os.close(slave)
            slave = None
            readers = [asyncio.create_task(drain(stream)) for stream in (process.stdout, process.stderr)]
            await self.until(lambda: (build / 'endpoint.json').exists() or process.returncode is not None)
            self.assertIsNone(process.returncode, output.decode(errors='replace'))
            endpoint = json.loads((build / 'endpoint.json').read_text())
            client = await Client.connect(endpoint['host'], endpoint['port'])
            async def state(predicate):
                for _ in range(500):
                    status, _ = await client.request('status')
                    execution = status['execution']
                    if predicate(execution):
                        return execution
                    await asyncio.sleep(.01)
                self.fail(output.decode(errors='replace'))
            initial = await state(lambda e: e['state'] == 'paused')
            os.write(master, b'step 1s\n')
            await state(lambda e: e['state'] == 'finished')
            os.write(master, b'reset\n')
            reset = await state(lambda e: e['epoch'] != initial['epoch'] and e['state'] == 'paused')
            os.write(master, b'break-time 20ms\nresume\n')
            breakpoint = await state(lambda e: e['pause_reason'] == 'breakpoint')
            self.assertEqual(breakpoint['position_ns'], '20000000')
            self.assertEqual(breakpoint['breakpoints_ns'], [])
            os.write(master, b'\x04')
            await asyncio.sleep(.1)
            self.assertIsNone(process.returncode)
            operation, _ = await client.request('simulation_command', epoch=reset['epoch'],
                command_id='web-after-terminal-eof', action='reset')
            await state(lambda e: e['epoch'] != reset['epoch'] and e['state'] == 'paused')
            process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                self.fail('shutdown timeout: ' + output.decode(errors='replace'))
            await asyncio.gather(*readers)
            self.assertEqual(process.returncode, 0, output.decode(errors='replace'))
            self.assertNotIn(b'Traceback', output)
        finally:
            if client:
                await client.close()
            if process and process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.wait(), 5)
            await asyncio.gather(*readers, return_exceptions=True)
            os.close(master)
            if slave is not None:
                os.close(slave)

    async def test_invalid_cli_script_returns_an_error_and_does_not_hang(self):
        import sys
        build = self.root / 'invalid-build'
        build.mkdir()
        plan_path = self.root / 'invalid-plan.json'
        plan_path.write_text(json.dumps(self.plan | {'build_dir': str(build)}))
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'ose.cli', 'run',
            '--mode', 'simulated', '--plan', str(plan_path), '--duration', '.1', '--command', 'step 0',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, error = await asyncio.wait_for(process.communicate(), 5)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn(b'positive integer', error)
            self.assertNotIn(b'Traceback', error)
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()
