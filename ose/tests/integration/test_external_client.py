import asyncio
import os
from pathlib import Path
import json
import tempfile
import sys
import threading
import unittest
from ose.codec.sdl2 import SDL2
from ose.execution.broker import Broker
from ose.client import Client, ClientError, Fifo, Latest, Retry, UnknownTopic, QoSError, PublicationRefused, CallbackFailed, SchemaError
from ose.delivery.policies import SubscriptionPolicy
from ose.modeling.compiler import build

SDL_ROOT = os.environ.get('SDL_ROOT')
CAT = 'SDL2|Measurement{1:uint64:sequence;2:fl64:value;}'

@unittest.skipUnless(SDL_ROOT, 'set SDL_ROOT')
class ExternalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.codec = SDL2(SDL_ROOT)
        # A public generated-style message; the client needs no checkout path.
        api = self.codec.api
        class Measurement(api.SdlMessage):
            _SDL_NAME = 'Measurement'
            _SDL_DESCRIPTOR = CAT
            _SDL_FIXED_SIZE = 16
            _SDL_FIELDS = ((1,'sequence','required','uint64',()),(2,'value','required','fl64',()))
        self.Message = Measurement
        self.broker = Broker(dict(sdl_root=SDL_ROOT, components={}, subscriptions=[], max_frame=1024*1024, max_datagram=1200))
        await self.broker.start()
        self.broker.enabled = True
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await asyncio.to_thread(client.close)
        await self.broker.close()

    async def client(self, name):
        c = await asyncio.to_thread(Client.connect, port=self.broker.port, client_id=name)
        self.clients.append(c)
        return c

    async def test_typed_fifo_udp_latest_and_cleanup(self):
        p = await self.client('publisher')
        publisher = await asyncio.to_thread(p.publisher, 'capteur.mesure', self.Message)
        for transport, qos in [('tcp',Fifo(ack='received',retry=Retry(ack_timeout_ms=20))),('udp',Latest(ack='received',retry=Retry(ack_timeout_ms=20)))]:
            c = await self.client(transport)
            received = []; available=threading.Condition()
            def callback(value,info):
                with available:
                    received.append((value,info));available.notify_all()
            sub = await asyncio.to_thread(c.subscribe,'capteur.mesure',self.Message,callback,qos=qos,transport=transport)
            result = await asyncio.to_thread(publisher.publish,self.Message(sequence=7,value=3.5))
            self.assertTrue(result.accepted)
            # Latest may replay the previous publication before this one arrives.
            def wait_for_publication():
                with available:
                    return available.wait_for(lambda:any(info.publication_id==result.publication_id for _,info in received),2)
            self.assertTrue(await asyncio.to_thread(wait_for_publication))
            value,info=next(pair for pair in received if pair[1].publication_id==result.publication_id)
            self.assertEqual(value.value,3.5)
            self.assertEqual(info.publication_id,result.publication_id)
            await asyncio.to_thread(sub.close)
        c = await self.client('late')
        event=threading.Event();values=[]
        def callback(value,info): values.append(value.sequence);event.set()
        sub=await asyncio.to_thread(c.subscribe,'capteur.mesure',self.Message,callback,qos=Latest(),transport='udp')
        self.assertTrue(await asyncio.to_thread(event.wait,2))
        self.assertEqual(values,[7])
        topics=await asyncio.to_thread(c.list_topics)
        self.assertEqual(topics[0]['topic'],'capteur.mesure')
        self.assertEqual(self.broker.ready,set())
        await asyncio.to_thread(c.close)
        await asyncio.sleep(.1)
        self.assertEqual(len(self.broker.external_tasks),0)
        self.assertEqual(len(self.broker.engines),0)

    async def test_errors_ownership_and_callback_failure(self):
        c=await self.client('one')
        with self.assertRaises(UnknownTopic):
            await asyncio.to_thread(c.subscribe,'missing',self.Message,lambda v,i:None)
        pub=await asyncio.to_thread(c.publisher,'topic',self.Message)
        other=await self.client('two')
        with self.assertRaises(PublicationRefused):
            await asyncio.to_thread(other.publisher,'topic',self.Message)
        with self.assertRaises(QoSError):
            await asyncio.to_thread(other.subscribe,'topic',self.Message,lambda v,i:None,qos=Fifo(capacity=999999))
        def fail(v,i): raise RuntimeError('user failed')
        await asyncio.to_thread(other.subscribe,'topic',self.Message,fail)
        await asyncio.to_thread(pub.publish,self.Message(sequence=1,value=1.))
        with self.assertRaisesRegex(CallbackFailed,'user failed'):
            await asyncio.wait_for(asyncio.to_thread(other.run),3)

    async def test_application_ids_preserve_absence_zero_and_all_bits_over_tcp_and_udp(self):
        producer = await self.client('id-producer')
        consumer = await self.client('id-consumer')
        identities = [None, 0, (1 << 64) - 1, 0, (1 << 53) + 1, (1 << 64) - 1, None]
        for transport in ('tcp', 'udp'):
            with self.subTest(transport=transport):
                topic = 'ids.' + transport
                publisher = await asyncio.to_thread(producer.publisher, topic, self.Message)
                received, condition = [], threading.Condition()

                def callback(value, info):
                    with condition:
                        received.append((value.sequence, info))
                        condition.notify_all()

                sub = await asyncio.to_thread(consumer.subscribe, topic, self.Message, callback,
                    qos=Fifo(ack='received', retry=Retry(ack_timeout_ms=30)), transport=transport)
                publications = []
                for index, identity in enumerate(identities, 1):
                    result = await asyncio.to_thread(publisher.publish, self.Message(sequence=index, value=3.5), id=identity)
                    self.assertTrue(result.accepted)
                    publications.append(result.publication_id)
                    def delivered():
                        with condition:
                            return condition.wait_for(lambda: len(received) >= index, 3)
                    self.assertTrue(await asyncio.to_thread(delivered))
                self.assertEqual([value for value, _ in received], list(range(1, 8)))
                self.assertEqual([info.app_id for _, info in received], identities)
                self.assertEqual([info.publication_id for _, info in received], publications)
                self.assertEqual(len(set(publications)), len(identities))
                await asyncio.to_thread(sub.close)
                # Retained latest state preserves the same optional metadata.
                for identity in (0, (1 << 64) - 1, None):
                    await asyncio.to_thread(publisher.publish, self.Message(sequence=9, value=3.5), id=identity)
                    event, latest = threading.Event(), []
                    def retain(value, info):
                        latest.append(info.app_id)
                        event.set()
                    retained = await asyncio.to_thread(consumer.subscribe, topic, self.Message, retain,
                        qos=Latest(ack='received', retry=Retry(ack_timeout_ms=30)), transport=transport)
                    self.assertTrue(await asyncio.to_thread(event.wait, 3))
                    self.assertEqual(latest, [identity])
                    await asyncio.to_thread(retained.close)

    async def test_invalid_application_ids_fail_before_publication_and_leave_the_client_usable(self):
        client = await self.client('invalid-id')
        publisher = await asyncio.to_thread(client.publisher, 'ids.invalid', self.Message)
        initial = self.broker.sequence
        for identity in (True, False, 1., -1, 1 << 64, '42'):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                await asyncio.to_thread(publisher.publish, self.Message(sequence=1, value=1.), id=identity)
        self.assertEqual(self.broker.sequence, initial)
        result = await asyncio.to_thread(publisher.publish, self.Message(sequence=1, value=1.), id=0)
        self.assertTrue(result.accepted)
        self.assertEqual(result.sequence, initial + 1)

    async def test_topic_routes_track_subscriptions_unsubscribe_and_disconnect(self):
        producer = await self.client('route-producer')
        first = await asyncio.to_thread(producer.publisher, 'first', self.Message)
        second = await asyncio.to_thread(producer.publisher, 'second', self.Message)
        self.assertEqual((await asyncio.to_thread(first.publish, self.Message(sequence=0, value=0.))).delivery, {})
        one, two = await self.client('route-one'), await self.client('route-two')
        values, other_values, shared_values = [], [], []
        received, other_received, shared_received = threading.Event(), threading.Event(), threading.Event()

        def receive(value, info):
            values.append(value.sequence)
            received.set()

        def receive_other(value, info):
            other_values.append(value.sequence)
            other_received.set()

        def receive_shared(value, info):
            shared_values.append(value.sequence)
            shared_received.set()

        sub = await asyncio.to_thread(one.subscribe, 'first', self.Message, receive)
        other = await asyncio.to_thread(two.subscribe, 'second', self.Message, receive_other)
        result = await asyncio.to_thread(first.publish, self.Message(sequence=1, value=1.))
        self.assertEqual(result.delivery, {sub.identity: 'accepted'})
        self.assertTrue(await asyncio.to_thread(received.wait, 2))
        self.assertEqual(other_values, [])
        result = await asyncio.to_thread(second.publish, self.Message(sequence=10, value=10.))
        self.assertEqual(result.delivery, {other.identity: 'accepted'})
        self.assertTrue(await asyncio.to_thread(other_received.wait, 2))

        shared = await asyncio.to_thread(two.subscribe, 'first', self.Message, receive_shared)
        received.clear()
        result = await asyncio.to_thread(first.publish, self.Message(sequence=2, value=2.))
        self.assertEqual(result.delivery, {sub.identity: 'accepted', shared.identity: 'accepted'})
        self.assertTrue(await asyncio.to_thread(received.wait, 2))
        self.assertTrue(await asyncio.to_thread(shared_received.wait, 2))
        await asyncio.to_thread(sub.close)
        shared_received.clear()
        result = await asyncio.to_thread(first.publish, self.Message(sequence=3, value=3.))
        self.assertEqual(result.delivery, {shared.identity: 'accepted'})
        self.assertTrue(await asyncio.to_thread(shared_received.wait, 2))
        await asyncio.to_thread(two.close)

        async def disconnected():
            while any(s.client_id == 'route-two' for s in self.broker.sessions.values()):
                await asyncio.sleep(.005)

        await asyncio.wait_for(disconnected(), 2)
        self.assertEqual((await asyncio.to_thread(first.publish, self.Message(sequence=4, value=4.))).delivery, {})
        self.assertEqual((await asyncio.to_thread(second.publish, self.Message(sequence=11, value=11.))).delivery, {})
        self.assertEqual(values, [1, 2])
        self.assertEqual(other_values, [10])
        self.assertEqual(shared_values, [2, 3])
        self.assertEqual(self.broker.subscriptions_by_topic, {})

    async def test_rejected_retained_state_leaves_no_route_and_can_be_retried(self):
        producer = await self.client('retained-producer')
        publisher = await asyncio.to_thread(producer.publisher, 'state', self.Message)
        await asyncio.to_thread(publisher.publish, self.Message(sequence=1, value=1.))
        consumer = await self.client('retained-consumer')
        self.broker.plan['max_datagram'] = 64
        with self.assertRaisesRegex(ClientError, 'retained state exceeds'):
            await asyncio.to_thread(consumer.subscribe, 'state', self.Message, lambda v, i: None,
                                    qos=Latest(), transport='udp')
        self.assertEqual(self.broker.subscriptions, {})
        self.assertEqual(self.broker.engines, {})
        self.assertEqual(self.broker.subscriptions_by_topic, {})
        result = await asyncio.to_thread(publisher.publish, self.Message(sequence=2, value=2.))
        self.assertEqual(result.delivery, {})
        received = threading.Event()
        values = []

        def receive(value, info):
            values.append(value.sequence)
            received.set()

        sub = await asyncio.to_thread(consumer.subscribe, 'state', self.Message, receive, qos=Latest())
        self.assertTrue(await asyncio.to_thread(received.wait, 2))
        self.assertEqual(values, [2])
        await asyncio.to_thread(sub.close)
        self.assertEqual(self.broker.subscriptions_by_topic, {})

    async def test_unsubscribe_releases_an_in_flight_publication(self):
        from ose.transport.tcp import Client as RPC
        producer = await self.client('pending-producer')
        publisher = await asyncio.to_thread(producer.publisher, 'pending', self.Message)
        control = await RPC.connect('127.0.0.1', self.broker.port)
        self.addAsyncCleanup(control.close)
        await control.request('client_hello', client_id='pending-consumer', udp_port=1)
        policy = SubscriptionPolicy(capacity=1, overflow='wait', wait_timeout_ms=1000)
        result, _ = await control.request('subscribe', CAT.encode(), topic='pending', root='Measurement',
                                         policy=policy.to_dict())
        identity = result['subscription']
        await asyncio.to_thread(publisher.publish, self.Message(sequence=1, value=1.))
        pending = asyncio.create_task(asyncio.to_thread(publisher.publish, self.Message(sequence=2, value=2.)))

        async def waiting():
            while not self.broker.engines[identity].buffer.metrics['refused']:
                await asyncio.sleep(.005)

        try:
            await asyncio.wait_for(waiting(), 1)
            self.assertFalse(pending.done())
            await control.request('unsubscribe', subscription=identity)
            result = await asyncio.wait_for(pending, 1)
            self.assertEqual(result.delivery, {identity: 'closed'})
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        result = await asyncio.to_thread(publisher.publish, self.Message(sequence=3, value=3.))
        self.assertEqual(result.delivery, {})
        self.assertEqual(self.broker.subscriptions_by_topic, {})

    async def test_slow_callback_does_not_block_control_or_other_subscription(self):
        c=await self.client('producer');p=await asyncio.to_thread(c.publisher,'topic',self.Message)
        consumer=await self.client('consumer');started=threading.Event();release=threading.Event();fast=threading.Event()
        def slow(v,i): started.set();release.wait(3)
        try:
            await asyncio.to_thread(consumer.subscribe,'topic',self.Message,slow,qos=Fifo(capacity=1,overflow='wait',ack='received',retry=Retry()))
            await asyncio.to_thread(consumer.subscribe,'topic',self.Message,lambda v,i:fast.set())
            await asyncio.to_thread(p.publish,self.Message(sequence=1,value=1.))
            self.assertTrue(await asyncio.to_thread(started.wait,2))
            fast.clear()
            for n in range(2,6):
                await asyncio.to_thread(p.publish,self.Message(sequence=n,value=float(n)))
            self.assertTrue(await asyncio.to_thread(fast.wait,2))
            self.assertEqual(len(await asyncio.to_thread(consumer.list_topics)),1)
        finally: release.set()

    async def test_external_model_binding_and_internal_topic_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'schema.sdl').write_text('message Measurement {1: required uint64 sequence;2: required fl64 value;}')
            (root/'handler.py').write_text('from schema import Measurement\ndef on_input(ctx, input):\n    msg=input.message\n    ctx.publish_result(Measurement(sequence=msg.sequence,value=msg.value*3), id=input.app_id)\n')
            model=dict(version=2,schemas='schema.sdl',external_topics={'external.input':{'root':'Measurement'}},
                components={'transform':dict(inputs={'input':{'root':'Measurement','activates':{'message':['result']}}},outputs={'result':{'root':'Measurement'}})},
                subscriptions=[{'id':'in','from_topic':'external.input','to':'transform.input'}])
            deployment=dict(version=1,components={'transform':{'container':{'language':'python','handler':'handler.py'}}})
            (root/'model.json').write_text(json.dumps(model));(root/'deployment.json').write_text(json.dumps(deployment))
            plan=await asyncio.to_thread(build,root/'model.json',root/'deployment.json',SDL_ROOT,root/'build')
            process=await asyncio.create_subprocess_exec(sys.executable,'-m','ose.cli','run','--plan',str(plan),'--duration','4',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try:
                line=await asyncio.wait_for(process.stdout.readline(),5)
                self.assertTrue(line,await process.stderr.read() if process.returncode is not None else 'no readiness')
                endpoint=json.loads(line)
                c=await asyncio.to_thread(Client.connect,port=endpoint['port'],client_id='injector')
                self.clients.append(c)
                pub=await asyncio.to_thread(c.publisher,'external.input',self.Message)
                with self.assertRaises(PublicationRefused):
                    await asyncio.to_thread(c.publisher,'transform.result',self.Message)
                event=threading.Event();received=[]
                def callback(value,info):received.append(value);event.set()
                await asyncio.to_thread(c.subscribe,'transform.result',self.Message,callback)
                await asyncio.to_thread(pub.publish,self.Message(sequence=4,value=2.))
                self.assertTrue(await asyncio.to_thread(event.wait,2))
                self.assertEqual(received[0].value,6.)
            finally:
                if process.returncode is None: process.terminate()
                communication=asyncio.create_task(process.communicate())
                try:
                    stdout,stderr=await asyncio.wait_for(asyncio.shield(communication),5)
                except asyncio.TimeoutError:
                    process.kill()
                    stdout,stderr=await communication
                    raise
                self.assertEqual(process.returncode,0,stderr.decode()+stdout.decode())

    async def test_each_schema_is_checked_and_publisher_reconnects(self):
        c=await self.client('contracts');p=await asyncio.to_thread(c.publisher,'topic',self.Message)
        await asyncio.to_thread(c.subscribe,'topic',self.Message,lambda v,i:None)
        api=self.codec.api
        class Extended(api.SdlMessage):
            _SDL_NAME='Measurement'
            _SDL_DESCRIPTOR='SDL2|Measurement{1:uint64:sequence;2:fl64:value;3:uint32:extra;}'
            _SDL_FIXED_SIZE=20
            _SDL_FIELDS=((1,'sequence','required','uint64',()),(2,'value','required','fl64',()),(3,'extra','required','uint32',()))
        with self.assertRaises(SchemaError):
            await asyncio.to_thread(c.subscribe,'topic',Extended,lambda v,i:None)
        await asyncio.to_thread(p.close)
        other=await self.client('replacement')
        replacement=await asyncio.to_thread(other.publisher,'topic',Extended)
        result=await asyncio.to_thread(replacement.publish,Extended(sequence=1,value=2.,extra=7))
        self.assertTrue(result.accepted)

    async def test_contract_changes_are_discovered_on_an_existing_subscription(self):
        producer = await self.client('dynamic-producer')
        consumer = await self.client('dynamic-consumer')
        publisher = await asyncio.to_thread(producer.publisher, 'dynamic', self.Message)
        received, available = [], threading.Condition()

        def callback(value, info):
            with available:
                received.append((value.sequence, value.value, info.publication_id))
                available.notify_all()

        subscription = await asyncio.to_thread(consumer.subscribe, 'dynamic', self.Message, callback)
        self.assertEqual(consumer.catalogues, {})
        api = self.codec.api

        class Extended(api.SdlMessage):
            _SDL_NAME = 'Measurement'
            _SDL_DESCRIPTOR = 'SDL2|Measurement{1:uint64:sequence;2:fl64:value;3:uint32:extra;}'
            _SDL_FIXED_SIZE = 20
            _SDL_FIELDS = ((1, 'sequence', 'required', 'uint64', ()),
                           (2, 'value', 'required', 'fl64', ()),
                           (3, 'extra', 'required', 'uint32', ()))

        publications = []
        for number, message_type in enumerate((self.Message, Extended, self.Message), 1):
            if number > 1:
                await asyncio.to_thread(publisher.close)
                publisher = await asyncio.to_thread(producer.publisher, 'dynamic', message_type)
            fields = dict(sequence=number, value=float(number))
            if message_type is Extended:
                fields['extra'] = 7
            result = await asyncio.to_thread(publisher.publish, message_type(**fields))
            self.assertTrue(result.accepted)
            publications.append(result.publication_id)

            def wait_for_callback():
                with available:
                    return available.wait_for(lambda: len(received) >= number, 2)

            self.assertTrue(await asyncio.to_thread(wait_for_callback))
            # The same subscription changes its decoder per message catalogue,
            # including when a previously seen catalogue returns on the route.
            self.assertEqual(subscription.codec.decoder[0], message_type._SDL_DESCRIPTOR)

        self.assertEqual(received, [(n, float(n), identity)
                                    for n, identity in enumerate(publications, 1)])
        self.assertEqual(set(consumer.catalogues),
                         {self.codec.identity(CAT), self.codec.identity(Extended._SDL_DESCRIPTOR)})
        self.assertEqual(len(consumer.subscriptions), 1)

    async def test_fifo_has_no_replay_and_udp_retries_deduplicate(self):
        p=await self.client('producer');publisher=await asyncio.to_thread(p.publisher,'topic',self.Message)
        await asyncio.to_thread(publisher.publish,self.Message(sequence=0,value=0.))
        c=await self.client('receiver');values=[];identities=[];event=threading.Event()
        def callback(value,info):values.append(value.sequence);identities.append(info.app_id);event.set()
        sub=await asyncio.to_thread(c.subscribe,'topic',self.Message,callback,transport='udp',qos=Fifo(ack='received',retry=Retry(ack_timeout_ms=20)))
        self.assertFalse(await asyncio.to_thread(event.wait,.05))
        engine=self.broker.engines[sub.identity]
        now=[0.]
        engine.clock=lambda:now[0]
        async def wait_for_metric(metric,count):
            async def wait():
                while engine.metrics[metric]<count:
                    await asyncio.sleep(.005)
            await asyncio.wait_for(wait(),3)
        original=self.broker.udp.send;attempts=[0]
        async def lose_first(header,payload=b'',address=None):
            attempts[0]+=1
            if attempts[0]>1:await original(header,payload,address)
        self.broker.udp.send=lose_first
        original_ack=c.udp.send;acks=[0]
        async def lose_first_ack(header,payload=b'',address=None):
            acks[0]+=1
            if acks[0]>1:await original_ack(header,payload,address)
        c.udp.send=lose_first_ack
        await asyncio.to_thread(publisher.publish,self.Message(sequence=1,value=1.),id=(1 << 64) - 1)
        await wait_for_metric('sent',1)
        # Advance retry time explicitly; socket and codec scheduling must not
        # determine how many attempts occur before an ACK is processed.
        now[0]+=1;engine.store.notify()
        self.assertTrue(await asyncio.to_thread(event.wait,2))
        await wait_for_metric('sent',2)
        now[0]+=1;engine.store.notify()
        await wait_for_metric('acknowledged',1)
        stats=await asyncio.to_thread(sub.stats)
        self.assertEqual(values,[1]);self.assertEqual(attempts[0],3)
        self.assertEqual(identities,[(1 << 64) - 1])
        self.assertEqual(acks[0],2)
        self.assertEqual(stats['client']['duplicates'],1)

    async def test_concurrent_publication_ids_keep_their_sequence(self):
        c=await self.client('concurrent');p=await asyncio.to_thread(c.publisher,'topic',self.Message)
        # Keep a bounded wait pending without attaching a data channel.
        policy=SubscriptionPolicy(capacity=1,overflow='wait',wait_timeout_ms=100)
        from ose.delivery.engine import DeliveryEngine
        from ose.delivery.stores import MemoryStore
        self.broker.subscriptions['unattached']={'id':'unattached','from':'topic','to':'absent.input','policy':policy.to_dict()}
        self.broker.subscriptions_by_topic.setdefault('topic', {})['unattached'] = self.broker.subscriptions['unattached']
        self.broker.engines['unattached']=DeliveryEngine(policy,MemoryStore(1))
        first=await asyncio.to_thread(p.publish,self.Message(sequence=1,value=1.))
        results=await asyncio.gather(*(asyncio.to_thread(p.publish,self.Message(sequence=n,value=float(n))) for n in range(2,6)))
        self.assertEqual(first.sequence,1)
        # Executor threads can reach the broker in a different order from the
        # gather arguments. Each reply still needs its own unique sequence.
        self.assertCountEqual([r.sequence for r in results],range(2,6))
        for r in [first,*results]:self.assertEqual(r.publication_id,f'{r.epoch}:{r.sequence}')

    async def test_external_publisher_wait_includes_long_internal_admission(self):
        port={'root':'Measurement','catalogue':CAT,'catalogue_id':self.codec.identity(CAT)}
        self.broker.plan['components']['offline']={'inputs':{'input':port},'outputs':{}}
        policy=SubscriptionPolicy(capacity=1,overflow='wait',wait_timeout_ms=20000)
        from ose.delivery.engine import DeliveryEngine
        from ose.delivery.stores import MemoryStore
        self.broker.subscriptions['long']={'id':'long','from':'topic','to':'offline.input','policy':policy.to_dict()}
        self.broker.subscriptions_by_topic.setdefault('topic', {})['long'] = self.broker.subscriptions['long']
        self.broker.engines['long']=DeliveryEngine(policy,MemoryStore(1))
        c=await self.client('long-wait');p=await asyncio.to_thread(c.publisher,'topic',self.Message)
        await asyncio.to_thread(p.publish,self.Message(sequence=1,value=1.))
        pending=asyncio.create_task(asyncio.to_thread(p.publish,self.Message(sequence=2,value=2.)))
        await asyncio.sleep(.05)
        self.assertEqual(len(await asyncio.to_thread(c.list_topics)),1)
        result=await asyncio.wait_for(pending,23)
        self.assertEqual(result.delivery,{'long':'full'})
        self.assertIsNone(c.failure)
