import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from tests.measurement_components import build_component, check_component

SDL_ROOT = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parents[3] / 'sdl'))


class JavaMeasurementTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_context(self):
        with tempfile.TemporaryDirectory() as root:
            plan = await asyncio.to_thread(build_component, root, 'java', SDL_ROOT)
            await check_component(self, plan)
