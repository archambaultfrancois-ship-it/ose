import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from tests.log_components import build_component, check_component


class AdaLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_generated_context(self):
        root = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parents[3] / 'sdl'))
        with tempfile.TemporaryDirectory() as directory:
            plan = await asyncio.to_thread(build_component, directory, 'ada', root)
            await check_component(self, plan)
