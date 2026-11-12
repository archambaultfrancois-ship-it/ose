"""Aggregate captures retain actual schemas and independent producer identities."""
import asyncio
from tests.integration import test_open_producers as fixture


class OpenProducerRecordingTests(fixture.OpenProducerFixture):
    async def recording_exercise(self, distributed):
        await self.start(distributed)
        class Extended(self.codec.api.SdlMessage):
            _SDL_NAME = 'Measurement'
            _SDL_DESCRIPTOR = 'SDL2|Measurement{1:uint64:sequence;2:fl64:value;3:uint32:extra;}'
            _SDL_FIXED_SIZE = 20
            _SDL_FIELDS = (*self.Message._SDL_FIELDS, (3, 'extra', 'required', 'uint32', ()))
        one, two = await self.client('one'), await self.client('two')
        first = await asyncio.to_thread(one.publisher, 'source.signal', Extended)
        second = await asyncio.to_thread(two.publisher, 'source.signal', self.Message)
        from ose.recording.recorder import Recorder
        recorder = Recorder('127.0.0.1', self.server.port, distributed=distributed)
        await recorder.connect()
        try:
            recorder.selected = {'source.signal': 'value > 0'}
            capture = self.root / 'capture.pcapng'
            await recorder.start(capture)
            for publisher, message in ((first, Extended(sequence=1, value=101., extra=7)),
                                       (second, self.Message(sequence=1, value=102.)),
                                       (first, Extended(sequence=2, value=103., extra=9))):
                result = await asyncio.to_thread(publisher.publish, message, id=0)
                self.assertTrue(result.accepted)
            await asyncio.to_thread(first.close)
            await asyncio.to_thread(second.publish, self.Message(sequence=2, value=104.), id=(1 << 64) - 1)
            await self.until(lambda: len(self.rows()) == 4)
            status = await recorder.stop()
            self.assertIsNone(status['error'])
            from ose.recording.replay import recording_reader
            records = list(recording_reader(self.codec)(capture).iter_records())
            self.assertEqual(len(records), 4)
            self.assertEqual([row.decode().fields['value'] for row in records], [101., 102., 103., 104.])
            self.assertEqual(len({row.metadata['ose.producer'] for row in records}), 2)
            self.assertEqual(records[-1].metadata['ose.app_id'], str((1 << 64) - 1))
            self.assertEqual(len({row.channel.catalogue for row in records}), 2)
        finally:
            await recorder.close()

    async def test_local_recording_tracks_compatible_schemas_and_surviving_producer(self):
        await self.recording_exercise(False)

    async def test_distributed_recording_tracks_compatible_schemas_and_surviving_producer(self):
        await self.recording_exercise(True)
