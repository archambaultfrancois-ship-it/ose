"""Untrusted publications still require validation before any routing side effect."""
from pathlib import Path
import unittest

from ose.codec.sdl2 import SDL2
from ose.execution.simulation.clock import VirtualClock
from ose.execution.simulation.router import Router
from ose.execution.simulation.trace import Trace


class SimulationRouterTests(unittest.TestCase):
    def test_unvalidated_publications_reject_bad_envelopes_without_committing(self):
        codec = SDL2(Path(__file__).resolve().parents[3] / 'sdl')
        catalogue = 'SDL2|Value{1:uint32:count;}'
        desc = dict(catalogue=catalogue, root='Value')
        plan = dict(components={'source': dict(inputs={}, outputs={'out': desc})},
                    subscriptions=[], external_topics={'external': desc})
        clock = VirtualClock()
        trace = Trace(clock)
        router = Router(plan, codec, clock, trace)
        self.addCleanup(trace.close)
        self.addCleanup(router.close)
        valid = codec.wrap(catalogue, b'\x01\x00\x00\x00\x01')
        wrong_catalogue = codec.wrap('SDL2|Other{1:uint32:count;}', b'\x01\x00\x00\x00\x01')
        for topic in ('external', 'source.out'):
            router.publish(topic, valid, 'valid:' + topic, app_id=0)
            sequence, digest = router.sequence, trace.digest.hexdigest()
            for envelope in (valid[:-1], wrong_catalogue, b'invalid'):
                with self.subTest(topic=topic, envelope=envelope), self.assertRaises(ValueError):
                    router.publish(topic, envelope, 'invalid')
                self.assertEqual(router.sequence, sequence)
                self.assertEqual(trace.digest.hexdigest(), digest)
