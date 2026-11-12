"""Supervision acknowledgements belong to the requested activation version."""
import asyncio
import unittest
from ose.execution.components import ComponentStates, disabled_components


class ComponentStatesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plan = {'components': {'source': {}, 'sink': {}}}
        self.states = ComponentStates(self.plan, ['source'])

    async def test_stale_acknowledgement_cannot_confirm_a_later_activation(self):
        self.assertTrue(self.states.acknowledge('source', 0, False))
        transition = asyncio.create_task(self.states.set(['source'], True))
        await asyncio.sleep(0)
        self.assertFalse(self.states.acknowledge('source', 0, False))
        self.assertTrue(self.states.snapshot()['source']['pending'])
        self.assertFalse(transition.done())
        self.states.acknowledge('source', 1, True)
        result = await transition
        self.assertFalse(result['source']['pending'])
        self.states.disconnected('source')
        self.assertIsNone(self.states.snapshot()['source']['active'])

    async def test_matching_old_boolean_does_not_confirm_an_unacknowledged_version(self):
        self.states.acknowledge('source', 0, False)
        # Model a reversed request following a timed-out transition.
        self.states.desired['source'] = dict(enabled=False, version=2)
        pending = asyncio.create_task(self.states.set(['source'], False))
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        self.states.acknowledge('source', 2, False)
        await pending

    async def test_unknown_names_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown components'):
            disabled_components(self.plan, ['missing'])
        with self.assertRaisesRegex(ValueError, 'known components'):
            await self.states.set(['missing'], False)

    async def test_unrelated_acknowledgement_does_not_restart_the_poll_wait(self):
        polling = asyncio.create_task(self.states.poll('sink', after=0))
        await asyncio.sleep(0)
        self.states.acknowledge('source', 0, False)
        state = await asyncio.wait_for(polling, .2)
        self.assertEqual(state, dict(enabled=True, version=0))
