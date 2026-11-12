"""Independent producers feed normal model topics in local and direct distributed execution."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from ose.client import Client, SchemaError
from ose.codec.sdl2 import SDL2
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator, Worker
from ose.modeling.compiler import build
from ose.transport.secure import connect

SDL_ROOT = os.environ.get('SDL_ROOT')
CAT = 'SDL2|Measurement{1:uint64:sequence;2:fl64:value;}'


@unittest.skipUnless(SDL_ROOT, 'requires SDL_ROOT')
class OpenProducerFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ose open producers ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.codec = SDL2(SDL_ROOT)
        class Measurement(self.codec.api.SdlMessage):
            _SDL_NAME = 'Measurement'
            _SDL_DESCRIPTOR = CAT
            _SDL_FIXED_SIZE = 16
            _SDL_FIELDS = ((1, 'sequence', 'required', 'uint64', ()), (2, 'value', 'required', 'fl64', ()))
        self.Message = Measurement
        (self.root / 'measurement.sdl').write_text('message Measurement { uint64 sequence; fl64 value; }')
        (self.root / 'source.py').write_text('''from pathlib import Path
from measurement import Measurement
count = 0
def on_start(ctx):
    with Path('starts').open('a') as stream: stream.write('start\\n')
def on_clock(ctx, input):
    global count
    count += 1
    ctx.publish_signal(Measurement(sequence=count, value=1.0))
''')
        (self.root / 'sink.py').write_text('''from pathlib import Path
import json
def on_input(ctx, input):
    with Path('events.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(sequence=input.message.sequence, value=input.message.value, app_id=input.app_id)) + '\\n')
''')
        model = dict(version=2, schemas='measurement.sdl', components={
            'source': dict(inputs={'clock': dict(kind='timer', timer=dict(mode='periodic', delay=.05),
                activates={'timeout': ['signal']})}, outputs={'signal': {'root': 'Measurement'}}),
            'sink': dict(inputs={'input': {'root': 'Measurement'}}, outputs={})},
            subscriptions=[dict(id='link', **{'from': 'source.signal', 'to': 'sink.input'})])
        (self.root / 'model.json').write_text(json.dumps(model))
        self.clients, self.tasks, self.workers = [], [], []
        self.server = None
        self.addAsyncCleanup(self.cleanup)

    async def cleanup(self):
        for client in self.clients:
            await asyncio.to_thread(client.close)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.server:
            await self.server.close()

    async def start(self, distributed=False, disabled=('source',)):
        deployment = dict(version=1, execution='distributed' if distributed else 'local',
            components={name: dict(container=dict(language='python', handler=name + '.py')) for name in ('source', 'sink')},
            subscriptions={'link': dict(routing='direct', transport='tcp')} if distributed else {})
        (self.root / 'deployment.json').write_text(json.dumps(deployment))
        path = await asyncio.to_thread(build, self.root / 'model.json', self.root / 'deployment.json', SDL_ROOT, self.root / 'build')
        self.plan = json.loads(path.read_text())
        if distributed:
            self.server = Coordinator(self.plan, lease_seconds=6, disabled=disabled)
            await self.server.start()
            for name in self.plan['components']:
                worker = Worker(self.plan, name, '127.0.0.1', self.server.port)
                self.workers.append(worker)
                self.tasks.append(asyncio.create_task(worker.run()))
            await self.until(lambda: self.server.activated)
        else:
            from ose.execution.adapter import Adapter
            self.server = Broker(self.plan, disabled=disabled)
            await self.server.start()
            for name in self.plan['components']:
                self.tasks.append(asyncio.create_task(Adapter(self.plan, name).run('127.0.0.1', self.server.port)))
            await self.until(lambda: self.server.ready == set(self.plan['components']))
            self.server.enabled = True

    async def client(self, name):
        client = await asyncio.to_thread(Client.connect, port=self.server.port, client_id=name)
        self.clients.append(client)
        return client

    def rows(self):
        file = self.root / 'events.jsonl'
        return [json.loads(line) for line in file.read_text().splitlines()] if file.exists() else []

    async def until(self, predicate):
        async def wait():
            while not predicate():
                for task in self.tasks:
                    if task.done():
                        task.result()
                await asyncio.sleep(.02)
        await asyncio.wait_for(wait(), 20)


class OpenProducerTests(OpenProducerFixture):
    async def exercise(self, distributed):
        await self.start(distributed)
        self.assertFalse((self.root / 'starts').exists())
        async def component_cli(operation):
            options = ['--distributed', '--insecure'] if distributed else []
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'ose.cli', 'component', operation, 'source',
                '--host', '127.0.0.1', '--port', str(self.server.port), *options,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            output, error = await asyncio.wait_for(process.communicate(), 30)
            self.assertEqual(process.returncode, 0, error.decode())
            return json.loads(output)
        self.assertFalse((await component_cli('status'))['source']['active'])
        one, two = await self.client('one'), await self.client('two')
        first = await asyncio.to_thread(one.publisher, 'source.signal', self.Message)
        second = await asyncio.to_thread(two.publisher, 'source.signal', self.Message)
        from ose.codec.catalogue import CatalogueTypes
        incompatible = CatalogueTypes(self.codec.api, 'SDL2|Measurement{1:uint64:sequence;2:string:value;}')
        third = await self.client('incompatible')
        with self.assertRaises((SchemaError, ValueError)):
            await asyncio.to_thread(third.publisher, 'source.signal', incompatible.types['Measurement'])
        for publisher, value in ((first, 101.), (second, 102.)):
            result = await asyncio.to_thread(publisher.publish, self.Message(sequence=1, value=value), id=0)
            self.assertTrue(result.accepted)
        await self.until(lambda: {101., 102.} <= {row['value'] for row in self.rows()})
        await asyncio.to_thread(first.close)
        result = await asyncio.to_thread(second.publish, self.Message(sequence=2, value=103.), id=(1 << 64) - 1)
        self.assertTrue(result.accepted)
        await self.until(lambda: any(row['app_id'] == (1 << 64) - 1 for row in self.rows()))
        control = await connect('127.0.0.1', self.server.port, 1024 * 1024)
        try:
            await component_cli('enable')
            await self.until(lambda: any(row['value'] == 1 for row in self.rows()))
            result = await asyncio.to_thread(second.publish, self.Message(sequence=3, value=105.))
            self.assertTrue(result.accepted)
            await self.until(lambda: any(row['value'] == 105. for row in self.rows()))
            await control.request('component_set', components=['source'], enabled=False, timeout=25)
            await asyncio.sleep(.2)  # Drain already admitted publications.
            before = len(self.rows())
            await asyncio.sleep(.2)
            self.assertEqual(len(self.rows()), before)
            await control.request('component_set', components=['sink'], enabled=False, timeout=25)
            refused = await asyncio.to_thread(second.publish, self.Message(sequence=4, value=106.))
            self.assertEqual(set(refused.delivery.values()), {'disabled'})
            await control.request('component_set', components=['sink'], enabled=True, timeout=25)
            await asyncio.to_thread(second.publish, self.Message(sequence=5, value=107.))
            await self.until(lambda: any(row['value'] == 107. for row in self.rows()))
            self.assertFalse(any(row['value'] == 106. for row in self.rows()))
            sequence = max(row['sequence'] for row in self.rows() if row['value'] == 1)
            await control.request('component_set', components=['source'], enabled=True, timeout=25)
            await self.until(lambda: any(row['value'] == 1 and row['sequence'] > sequence for row in self.rows()))
            self.assertEqual((self.root / 'starts').read_text().splitlines(), ['start'])
        finally:
            await control.close()

    async def test_local_model_topics_and_callback_boundary_supervision(self):
        await self.exercise(False)

    async def test_distributed_direct_topics_and_callback_boundary_supervision(self):
        await self.exercise(True)
