import io
import json
import unittest

from ose.recording.console import Console
from ose.recording.recorder import Recorder
from sdl_recording import RecordingReader
from tests.integration.test_fanin import FaninFixture


class InputRecordingTests(FaninFixture, unittest.IsolatedAsyncioTestCase):
    sink_output = True

    async def capture(self, *, distributed=False, latest=False, collision=False, format="pcapng", filtered=False):
        await self.start({"buffer": "latest"} if latest else {"capacity": 2}, distributed=distributed)
        recorder = Recorder("127.0.0.1", self.server.port, distributed=distributed)
        await recorder.connect()
        self.addAsyncCleanup(recorder.close)
        console = Console(recorder, io.StringIO())
        await console.execute("list --kind input")
        self.assertIn("sink.input  input", console.output.getvalue())
        self.assertNotIn("input:sink.input", console.output.getvalue())
        await console.execute("select --kind input sink.input")
        key = next(iter(recorder.selected))
        desc = await recorder.describe("sink.input", kind="input")
        self.assertEqual((desc["topic"], desc["kind"], desc["observation"]), ("sink.input", "input", "tick"))
        self.assertEqual(desc["sources"], ["left.out", "right.out"])
        await console.execute("show --kind input sink.input")
        self.assertIn("Sources: left.out, right.out", console.output.getvalue())
        if filtered:
            await console.execute("filter --kind input sink.input sequence >= 20")
        if collision:
            await console.execute("select --kind output sink.input")
            self.assertEqual(len(recorder.selected), 2)
        path = self.path.parent / ("capture." + format)
        capture = await recorder.start(path, format=format)
        if not distributed:
            await self.until(lambda: "input" in self.sink.component_recording.active_inputs)
        # Source events alone must not appear until an activation selects them.
        for source, value in (("left", 10), ("right", 20), ("left", 30)):
            await self.publish(source, value)
        self.assertEqual(capture.topic_counts[key], 0)
        callback_values = [30] if latest else [10, 20]
        expected = [20] if filtered else callback_values
        await self.resume(callback_values)
        await self.until(lambda: capture.topic_counts[key] >= len(expected))
        if latest:
            # The retained latest value does not reactivate a Push input.
            # New receptions do, even when their payload is unchanged.
            for count in (2, 3):
                await self.publish("left", 30)
                await self.until(lambda: capture.topic_counts[key] >= count)
                await self.until(lambda: len(self.values()) >= count)
            expected = [30, 30, 30]
            self.assertEqual(self.values(), expected)
        if collision:
            desc = self.sink.node["outputs"]["input"]
            from ose.codec.catalogue import CatalogueTypes
            types = CatalogueTypes(self.codec.api, desc["catalogue"])
            body = self.codec.wrap(desc["catalogue"], self.codec.api.encode(types.context,
                types.types["Measurement"](sequence=99, value=99.)))
            if distributed:
                await self.sink.publish("sink.input", body)
            else:
                await self.server.publish("sink.input", body)
            await self.until(lambda: capture.topic_counts["sink.input"] == 1)
        await recorder.stop()
        self.assertFalse(capture.error)
        reader = RecordingReader(path)
        reader.validate()
        records = list(reader.iter_records())
        inputs = [record for record in records if record.channel.metadata["ose.kind"] == "input"]
        values = [record.decode().fields["sequence"] for record in inputs]
        self.assertEqual(values, expected)
        self.assertEqual([record.sequence for record in inputs],
                         [2] if filtered else list(range(1, len(inputs) + 1)))
        if filtered:
            self.assertEqual(capture.status()["capture_counts"]["filtered"], 1)
        for record in inputs:
            self.assertEqual(record.channel.topic, "sink.input")
            self.assertEqual(record.channel.metadata["ose.observation"], "tick")
            self.assertEqual(json.loads(record.channel.metadata["ose.sources"]), ["left.out", "right.out"])
        if collision:
            outputs = [record for record in records if record.channel.metadata["ose.kind"] == "output"]
            self.assertEqual([record.decode().fields["sequence"] for record in outputs], [99])
            self.assertNotEqual(inputs[0].channel.id, outputs[0].channel.id)
            self.assertEqual(outputs[0].channel.topic, inputs[0].channel.topic)
        return recorder

    async def test_local_fifo_records_selected_messages_after_shared_overflow(self):
        await self.capture()

    async def test_local_latest_records_new_receptions_with_unchanged_values(self):
        await self.capture(latest=True, format="mcap")

    async def test_distributed_fifo_records_values_at_receiver_worker(self):
        await self.capture(distributed=True)

    async def test_distributed_latest_records_new_receptions_with_unchanged_values(self):
        await self.capture(distributed=True, latest=True)

    async def test_local_same_named_input_output_remain_distinct_channels(self):
        await self.capture(collision=True)

    async def test_distributed_same_named_input_output_remain_distinct_mcap_channels(self):
        await self.capture(distributed=True, collision=True, format="mcap")

    async def test_local_input_filters_apply_to_selected_values_without_changing_callbacks(self):
        await self.capture(filtered=True)

    async def test_distributed_input_filters_apply_at_receiver_without_changing_callbacks(self):
        await self.capture(distributed=True, filtered=True)
