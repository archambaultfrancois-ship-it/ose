"""Live console copies preserve raw logs, partial UTF-8 and shutdown output."""
import asyncio
import io
from pathlib import Path
import tempfile
import unittest

from ose.execution.local_cluster import _follow_log


class ClusterLogTests(unittest.IsolatedAsyncioTestCase):
    async def until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), 2)

    async def test_console_streams_before_shutdown_and_drains_unterminated_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'worker.log'
            path.write_bytes(b'')
            stopped, output = asyncio.Event(), io.StringIO()
            following = asyncio.create_task(_follow_log('worker', path, stopped, output))
            try:
                with path.open('ab', buffering=0) as writer:
                    writer.write(b'first\ncaf\xc3')
                    await self.until(lambda: output.getvalue() == '[worker] first\n')
                    writer.write(b'\xa9\nrestarted\nlast')
                    await self.until(lambda: '[worker] restarted\n' in output.getvalue())
                    self.assertFalse(following.done())
                    self.assertNotIn('last', output.getvalue())
                    # Output written during shutdown must be drained too.
                    writer.write(b' line')
            finally:
                stopped.set()
                await asyncio.wait_for(following, 2)
            self.assertEqual(output.getvalue(),
                             '[worker] first\n[worker] café\n[worker] restarted\n[worker] last line\n')
            self.assertEqual(path.read_bytes(), b'first\ncaf\xc3\xa9\nrestarted\nlast line')

    async def test_large_and_invalid_utf8_logs_drain_without_truncating_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'worker.log'
            payload = b'a' * 150000 + b'\ninvalid: \xff\n'
            path.write_bytes(payload)
            stopped, output = asyncio.Event(), io.StringIO()
            stopped.set()
            await _follow_log('worker', path, stopped, output)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(output.getvalue().count('a'), 150001)
            self.assertIn('[worker] invalid: \ufffd\n', output.getvalue())
