"""Replay selection rejects ambiguous mappings before loading optional services."""
import asyncio
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from ose.recording.pacing import PlaybackClock, playback_speed
from ose.recording.replay import RepeatedEvents, loop_count, topic_mapping
from ose.execution.simulation.scenario import Event, read_scenario


class ReplayMappingTests(unittest.TestCase):
    def test_explicit_selection_and_external_destinations(self):
        self.assertEqual(topic_mapping(["sensor.out=external.signal"], {"external.signal": {}}),
                         {"sensor.out": "external.signal"})
        for mappings in ([], ["sensor.out"], ["=external.signal"], ["sensor.out="],
                         ["sensor.out=internal.output"],
                         ["sensor.out=external.signal", "sensor.out=external.other"],
                         ["a=external.signal", "b=external.signal"]):
            with self.subTest(mappings=mappings), self.assertRaises(ValueError):
                topic_mapping(mappings, {"external.signal": {}, "external.other": {}})

    def test_scenario_ids_are_optional_uint64_and_independent_of_event_indices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'message.s2ps').write_bytes(b'opaque envelope')
            path = root / 'scenario.json'
            events = [dict(at=i, topic='external', envelope='message.s2ps', **extra)
                      for i, extra in enumerate(({}, {'app_id': 0}, {'app_id': (1 << 64) - 1}, {'app_id': 0}))]
            path.write_text(json.dumps(dict(version=1, events=events)))
            result = read_scenario(path, {'external': dict(catalogue='catalogue', root='Value')}, Mock())
            self.assertEqual([event.app_id for event in result], [None, 0, (1 << 64) - 1, 0])
            self.assertEqual([event.index for event in result], [0, 1, 2, 3])
            for value in (None, True, -1, 1 << 64, '1', 1.0):
                events[0]['app_id'] = value
                path.write_text(json.dumps(dict(version=1, events=events)))
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'uint64'):
                    read_scenario(path, {'external': dict(catalogue='catalogue', root='Value')}, Mock())


class PlaybackClockTests(unittest.IsolatedAsyncioTestCase):
    async def test_speed_changes_and_pause_preserve_timeline_position(self):
        timestamp = 0
        clock = PlaybackClock(2, now=lambda: timestamp)
        self.assertEqual(await clock.wait(0), 0)
        timestamp = .75
        self.assertEqual(await clock.wait(1_000_000_000), .25)
        clock.pause()
        timestamp = 10
        self.assertEqual(clock.position(), 1_500_000_000)
        clock.set_speed(.5)
        self.assertTrue(clock.paused)
        clock.resume()
        timestamp = 11
        self.assertEqual(clock.position(), 2_000_000_000)
        self.assertEqual(await clock.wait(2_000_000_000), 0)

    async def test_changing_speed_wakes_pending_deadline_and_stop_wakes_pause(self):
        clock = PlaybackClock(.001)
        waiting = asyncio.create_task(clock.wait(10_000_000_000))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        clock.set_speed(0)
        self.assertEqual(await asyncio.wait_for(waiting, 1), 0)
        clock.pause()
        waiting = asyncio.create_task(clock.wait(20_000_000_000))
        await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        clock.stop()
        self.assertIsNone(await asyncio.wait_for(waiting, 1))

    def test_invalid_speed_is_rejected(self):
        for speed in (-1, float("nan"), float("inf"), True, None):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                playback_speed(speed)


class ReplayLoopTests(unittest.TestCase):
    def setUp(self):
        self.events = [Event(0, 1, "external", Path("first"), "a"),
                       Event(7, 0, "external", Path("second"), "b", 0)]

    def test_passes_shift_timing_and_ids_while_reusing_envelopes(self):
        repeated = RepeatedEvents(self.events, 3)
        self.assertEqual([event.at for event in repeated], [0, 7, 8, 15, 16, 23])
        self.assertEqual([event.index for event in repeated], [1, 0, 3, 2, 5, 4])
        self.assertEqual([event.app_id for event in repeated], [None, 0, None, 0, None, 0])
        self.assertEqual(repeated.input_paths, {Path("first"), Path("second")})
        self.assertEqual(repeated[4].path, self.events[0].path)
        self.assertEqual(repeated[4].digest, self.events[0].digest)
        with self.assertRaises(IndexError):
            repeated[6]

    def test_infinite_and_large_counts_do_not_expand_event_storage(self):
        infinite = RepeatedEvents(self.events, 0)
        self.assertIsNone(infinite.event_count)
        self.assertEqual([event.at for event in itertools.islice(infinite, 5)], [0, 7, 8, 15, 16])
        bounded = RepeatedEvents(self.events, 0, horizon=16)
        self.assertEqual(bounded.event_count, 6)
        large = RepeatedEvents(self.events, 10 ** 30)
        self.assertEqual(large.event_count, 2 * 10 ** 30)
        self.assertIs(large.events, self.events)
        self.assertEqual(large[2 * 10 ** 20].at, 8 * 10 ** 20)
        self.assertEqual(list(RepeatedEvents([], 0)), [])

    def test_invalid_loop_counts_are_rejected(self):
        for value in (-1, True, 1.5, "2", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                loop_count(value)
