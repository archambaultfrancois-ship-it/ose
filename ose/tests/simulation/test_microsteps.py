"""Real CH/2 subprocesses: same-time feedback, visibility and bounded stages."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from ose.codec.sdl2 import SDL2
from ose.delivery.policies import SubscriptionPolicy
from ose.execution.simulation import MicrostepLimitError, Simulation
from ose.modeling.compiler import build
from ose.parameters.values import Values

SDL_ROOT = os.environ.get('SDL_ROOT')
HANDLER = '''import time
from value import Value
calls = 0
def react(ctx, input):
    global calls
    time.sleep(ctx.parameters.get('delay', 0))
    calls += 1
    if ctx.parameters.get('timer_consumer'):
        previous = ctx.inbox('cache').pull(id=0)
        assert previous is not None
        assert ctx.inbox('cache').get(handle=previous.handle) is None
        return
    if ctx.parameters.get('read_cache'):
        inbox = ctx.inbox('cache')
        previous = inbox.get(id=0)
        assert (previous is None) == (calls == 1)
        if previous is not None and ctx.parameters.get('consume'):
            if ctx.parameters['consume'] == 'remove': assert inbox.remove(handle=previous.handle)
            else: assert inbox.pull(handle=previous.handle).app_id == 0
            assert inbox.get(handle=previous.handle) is None
    if ctx.parameters.get('fail_on') == calls:
        ctx.publish_out(Value(count=999), id=0)
        raise RuntimeError('injected callback failure')
    maximum = ctx.parameters.get('max_calls', 0)
    if maximum and calls >= maximum: return
    count = 0 if input.message is None or ctx.parameters.get('same_payload') else input.message.count + 1
    if input.message is not None: assert input.app_id == 0
    stop = ctx.parameters.get('stop_at', 6)
    if stop is None or count <= stop:
        for _ in range(ctx.parameters.get('copies', 1)):
            ctx.publish_out(Value(count=count), id=0)
on_clock = on_input = on_feedback = react
'''


@unittest.skipUnless(SDL_ROOT, 'set SDL_ROOT')
class MicrostepTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='ose microsteps ')
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        (cls.root / 'value.sdl').write_text('message Value {1: required uint32 count;}')
        (cls.root / 'handler.py').write_text(HANDLER)
        components, subscriptions = {}, []
        for source, target in (('a', 'b'), ('c', 'd')):
            components[source] = dict(inputs=dict(
                clock=dict(kind='timer', timer=dict(mode='one_shot', delay=.01), activates=dict(timeout=['out'])),
                feedback=dict(root='Value', activates=dict(message=['out'])), cache=dict(root='Value', mode='pull')),
                outputs=dict(out=dict(root='Value')))
            components[target] = dict(inputs=dict(input=dict(root='Value', activates=dict(message=['out'])),
                cache=dict(root='Value', mode='pull')), outputs=dict(out=dict(root='Value')))
            subscriptions += [dict(id=source, **{'from': source+'.out', 'to': target+'.input'}),
                              dict(id=target, **{'from': target+'.out', 'to': source+'.feedback'})]
        model = dict(version=2, schemas='value.sdl',
                     components=components, subscriptions=subscriptions)
        deployment = dict(version=1, components={name: dict(container=dict(language='python', handler='handler.py'))
                                                for name in components})
        for name, document in (('model', model), ('deployment', deployment)):
            (cls.root / (name+'.json')).write_text(json.dumps(document))
        cls.plan_path = build(cls.root/'model.json', cls.root/'deployment.json', SDL_ROOT, cls.root/'build')
        cls.template = json.loads(cls.plan_path.read_text())
        cls.codec = SDL2(SDL_ROOT)

    def plan(self, *, pair=False, **parameters):
        plan = copy.deepcopy(self.template)
        if pair:
            plan['components'] = {name: plan['components'][name] for name in ('a', 'b')}
            plan['subscriptions'] = [sub for sub in plan['subscriptions'] if sub['id'] in ('a', 'b')]
        for node in plan['components'].values():
            node['parameters'] = dict(parameters)
        return plan

    def engine(self, plan, *, workers=1, duration=.1, max_microsteps=None):
        path = self.root / (self._testMethodName + '-' + str(workers) + '.jsonl')
        return Simulation(plan, duration, workers=workers, trace=path, microsteps=True, max_microsteps=max_microsteps), path

    @staticmethod
    def trace(path, event=None):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        return [row for row in rows if event is None or row['event'] == event]

    def scenario(self, plan, counts, *, policy=None, at=.01):
        desc = plan['components']['b']['inputs']['input']
        plan['external_topics'] = {'external': {key: desc[key] for key in ('root', 'catalogue', 'catalogue_id')}}
        plan['subscriptions'].append(dict(id='external', **{'from': 'external', 'from_topic': 'external',
            'to': 'b.input', 'policy': SubscriptionPolicy.resolve(policy).to_dict()}))
        values = Values(self.codec.api, desc['catalogue'], 'Value')
        events = []
        for index, count in enumerate(counts):
            file = self.root / f'event-{index}.s2ps'
            file.write_bytes(self.codec.wrap(desc['catalogue'], values.encode(dict(count=count))))
            events.append(dict(at=at, topic='external', envelope=str(file), app_id=0))
        path = self.root / 'scenario.json'
        path.write_text(json.dumps(dict(version=1, events=events)))
        return path

    async def test_activating_feedback_has_identical_traces_with_one_and_four_workers(self):
        traces = []
        for workers in (1, 4):
            plan = self.plan()
            self.assertNotIn('simulation', plan)
            for name in ('a', 'c'):
                plan['components'][name]['parameters']['delay'] = .003 if (name == 'a') == (workers == 1) else .0001
            engine, path = self.engine(plan, workers=workers)
            summary = await engine.execute()
            self.assertEqual((summary['ticks'], summary['publications'], summary['stop_reason']), (16, 14, 'quiescent'))
            waves = self.trace(path, 'wave')
            self.assertEqual([row['microstep'] for row in waves], list(range(8)))
            self.assertTrue(all(row['time_ns'] == 10_000_000 and len(row['components']) == 2 for row in waves))
            self.assertTrue(all(row['app_id'] == 0 for row in self.trace(path, 'publication')))
            self.assertEqual(self.trace(path, 'publication')[0]['microstep'], 1)
            traces.append(path.read_bytes())
        self.assertEqual(traces[0], traces[1])

    async def test_equal_payloads_can_terminate_through_component_state(self):
        plan = self.plan(same_payload=True, max_calls=4)
        engine, path = self.engine(plan, workers=4)
        summary = await engine.execute()
        self.assertEqual((summary['ticks'], summary['stop_reason']), (14, 'quiescent'))
        self.assertEqual(len({row['sha256'] for row in self.trace(path, 'publication')}), 1)

    async def test_infinite_equal_payload_feedback_reports_the_limit_and_pending_work(self):
        for workers in (1, 4):
            plan = self.plan(same_payload=True, stop_at=None)
            engine, path = self.engine(plan, workers=workers, max_microsteps=3)
            with self.assertRaisesRegex(MicrostepLimitError, 'time_ns=10000000, microstep=3') as caught:
                await engine.execute()
            self.assertEqual(engine.summary['stop_reason'], 'microstep_limit')
            diagnostic = caught.exception.diagnostic
            self.assertEqual(engine.summary['microstep_diagnostic'], diagnostic)
            self.assertEqual(set(diagnostic['pending_activations']), {'b', 'd'})
            self.assertIn('a.feedback:message', diagnostic['activation_cycle'])
            self.assertIn('a.out:publish', diagnostic['activation_cycle'])
            self.assertTrue(all(process.returncode == 0 for process in engine.processes.values()))
            self.assertEqual(self.trace(path)[-1]['reason'], 'microstep_limit')
            saved = json.loads((self.root/'build/last-status.json').read_text())
            self.assertEqual(saved['microstep_diagnostic'], diagnostic)

    async def test_last_allowed_activation_can_finish_without_a_false_limit_error(self):
        plan = self.plan(pair=True)
        engine, _ = self.engine(plan, max_microsteps=8)
        summary = await engine.execute()
        self.assertEqual((summary['waves'], summary['stop_reason']), (8, 'quiescent'))

    async def test_timers_keep_physical_cadence_and_reset_the_stage_limit(self):
        plan = self.plan(pair=True, stop_at=0)
        plan['components']['a']['inputs']['clock']['timer']['mode'] = 'periodic'
        engine, path = self.engine(plan, duration=.035, max_microsteps=2)
        summary = await engine.execute()
        self.assertEqual(summary['stop_reason'], 'duration')
        waves = self.trace(path, 'wave')
        self.assertEqual([(row['time_ns'], row['microstep']) for row in waves],
                         [(time, step) for time in (10_000_000, 20_000_000, 30_000_000) for step in (0, 1)])

    async def test_scenario_receipt_cancels_same_time_inactivity_but_not_an_independent_timer(self):
        plan = self.plan(pair=True, stop_at=0)
        plan['components'] = {'b': plan['components']['b']}; plan['subscriptions'] = []
        plan['components']['b']['inputs']['input'].update(timeout=.01, activates=dict(message=['out'], timeout=['out']))
        plan['components']['b']['inputs']['clock'] = dict(kind='timer', timer=dict(mode='one_shot', delay=.01),
                                                        activates=dict(timeout=['out']))
        scenario = self.scenario(plan, [0])
        engine = Simulation(plan, .02, scenario=scenario, trace=self.root/'tie.jsonl', microsteps=True)
        await engine.execute()
        activations = self.trace(self.root/'tie.jsonl', 'tick')
        self.assertEqual([(row['activation_event'], list(row['inputs'])) for row in activations],
                         [('timeout', ['clock']), ('message', ['input'])])
        self.assertEqual([row['microstep'] for row in activations], [0, 1])

    async def test_fifo_wait_and_latest_keep_their_reception_semantics(self):
        for buffer in ('fifo', 'latest'):
            plan = self.plan(pair=True, stop_at=None)
            plan['components'] = {'b': plan['components']['b']}; plan['subscriptions'] = []
            settings = dict(buffer=buffer, **(dict(capacity=2, overflow='wait', wait_timeout_ms=100) if buffer == 'fifo' else {}))
            scenario = self.scenario(plan, [1, 2, 3], policy=settings)
            path = self.root / (buffer+'.jsonl')
            engine = Simulation(plan, .02, scenario=scenario, trace=path, microsteps=True)
            summary = await engine.execute()
            ticks = self.trace(path, 'tick')
            self.assertEqual([row['inputs']['input'] for row in ticks],
                             ['scenario:0', 'scenario:1', 'scenario:2'] if buffer == 'fifo' else ['scenario:2'])
            self.assertEqual([row['microstep'] for row in ticks], list(range(len(ticks))))
            self.assertEqual(summary['stop_reason'], 'quiescent')

    async def test_pull_visibility_consumption_and_failure_rollback(self):
        for consume in ('pull', 'remove'):
            plan = self.plan(pair=True)
            plan['subscriptions'].append(dict(id='cache', **{'from': 'a.out', 'to': 'a.cache',
                'policy': SubscriptionPolicy.resolve().to_dict()}))
            plan['components']['a']['parameters'] = dict(read_cache=True, consume=consume, fail_on=2)
            engine, path = self.engine(plan)
            with self.assertRaises(asyncio.IncompleteReadError):
                await engine.execute()
            self.assertEqual(engine.summary['inputs']['a']['cache']['depth'], 1)
            self.assertEqual(engine.summary['inputs']['a']['feedback']['depth'], 1)
            # Only the two successful callbacks publish; the failed callback's
            # staged 999 and its withdrawal are both cancelled.
            self.assertEqual(len(self.trace(path, 'publication')), 2)
            self.assertEqual(engine.summary['stop_reason'], 'failed')

    async def test_successful_peer_consumption_survives_another_callback_failure(self):
        plan = self.plan()
        for name in ('a', 'c'):
            plan['subscriptions'].append(dict(id=name+'-cache', **{'from': name+'.out', 'to': name+'.cache',
                'policy': SubscriptionPolicy.resolve().to_dict()}))
            plan['components'][name]['parameters'] = dict(read_cache=True, consume='pull')
        plan['components']['a']['parameters']['fail_on'] = 2
        committed = asyncio.Event()

        class OrderedCompletion(Simulation):
            async def _calculate_input(self, name, reference):
                # Force a peer's real FINISHED/consumption before the failing
                # callback, without relying on wall-clock sleeps.
                if name == 'a' and reference.port == 'feedback':
                    await committed.wait()
                result = await super()._calculate_input(name, reference)
                if name == 'c' and reference.port == 'feedback':
                    committed.set()
                return result

        path = self.root / 'peer-failure.jsonl'
        engine = OrderedCompletion(plan, .1, workers=4, microsteps=True, trace=path)
        with self.assertRaises(asyncio.IncompleteReadError):
            await engine.execute()
        self.assertTrue(committed.is_set())
        self.assertEqual(engine.summary['inputs']['a']['cache']['depth'], 1)
        self.assertEqual(engine.summary['inputs']['c']['cache']['depth'], 0)
        self.assertEqual(engine.summary['inputs']['c']['feedback']['depth'], 0)
        self.assertEqual(len(self.trace(path, 'publication')), 4)

    async def test_admission_wait_uses_real_deadlines_and_committed_pull_releases_capacity(self):
        plan = self.plan(pair=True, stop_at=0, copies=2)
        plan['components']['b']['inputs']['clock'] = dict(kind='timer', timer=dict(mode='one_shot', delay=.02))
        plan['components']['b']['parameters'] = dict(timer_consumer=True)
        plan['subscriptions'] = [dict(id='cache', **{'from': 'a.out', 'to': 'b.cache',
            'policy': SubscriptionPolicy.resolve(dict(capacity=1, overflow='wait', wait_timeout_ms=100)).to_dict()})]
        engine, path = self.engine(plan, max_microsteps=1)
        summary = await engine.execute()
        self.assertEqual(summary['stop_reason'], 'quiescent')
        self.assertEqual(summary['inputs']['b']['cache']['depth'], 1)
        self.assertEqual([(row['time_ns'], row['microstep']) for row in self.trace(path, 'wave')],
                         [(10_000_000, 0), (20_000_000, 0)])
        self.assertEqual([(row['time_ns'], row['microstep']) for row in self.trace(path, 'admission')],
                         [(10_000_000, 1), (20_000_000, 1)])
