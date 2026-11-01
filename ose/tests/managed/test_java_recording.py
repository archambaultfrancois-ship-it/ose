import os
import tempfile
import unittest
from pathlib import Path
from ose.codec.sdl2 import SDL2
from tests.internal_recording import build_component
from tests.integration.test_component_recording import check_component

SDL_ROOT = Path(os.environ.get("SDL_ROOT", Path(__file__).resolve().parents[3] / "sdl"))


class JavaRecordingTests(unittest.TestCase):
    def test_context_registry_and_live_activation(self):
        with tempfile.TemporaryDirectory() as root:
            check_component(self, build_component(root, "java", SDL_ROOT), SDL2(SDL_ROOT))


class JavaReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_compatible_recorded_schema(self):
        from tests.replay_schema import check_generated_replay
        with tempfile.TemporaryDirectory() as root:
            await check_generated_replay(self, root, SDL_ROOT, 'java')
