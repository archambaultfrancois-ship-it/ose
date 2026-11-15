"""Production batching and parameter cache preserve the SDK contract."""
import io
from types import SimpleNamespace
import unittest
from ose.execution.component_io import SessionIO
from ose.execution.context import ComponentContext
from ose.execution.ipc import frame


class CountingWriter(io.BytesIO):
    calls = 0
    limit = None

    def write(self, data):
        self.calls += 1
        return super().write(data if self.limit is None else data[:self.limit])


class AccelerationTests(unittest.TestCase):
    def batch_session(self):
        session = SessionIO.__new__(SessionIO)
        session.writer, session.reader = CountingWriter(), io.BytesIO()
        session.channel, session._in_callback = None, True
        session._pending = bytearray()
        return session

    def test_batch_preserves_every_frame_and_flushes_before_requests(self):
        session = self.batch_session()
        expected = b''
        for op, body in [('P', b'publication'), ('V', b'metric'), ('L', b'log'), ('C', b'record')]:
            session.send(op, body)
            expected += frame(op, body)
        self.assertEqual(session.writer.getvalue(), b'')
        session.send('Q', b'pull')
        expected += frame('Q', b'pull')
        self.assertEqual(session.writer.getvalue(), expected)
        self.assertEqual(session.writer.calls, 1)
        for boundary in ('S', 'F', 'R'):
            session.send('V', b'v')
            session.send(boundary)
            expected += frame('V', b'v') + frame(boundary)
            self.assertEqual(session.writer.getvalue(), expected)

    def test_batch_limits_memory_handles_partial_writes_and_flushes_on_close(self):
        session = self.batch_session()
        session.writer.limit = 97
        expected = b''
        for size in (1, 30000, 40000, 100000, 2):
            session.send('P', b'x' * size)
            expected += frame('P', b'x' * size)
            self.assertLess(len(session._pending), 65536)
        session.close()
        self.assertEqual(session.writer.getvalue(), expected)
        session._in_callback = False
        session.send('L', b'stop')
        self.assertEqual(session.writer.getvalue(), expected + frame('L', b'stop'))

    def test_batch_does_not_swallow_write_failures_or_frame_limits(self):
        session = self.batch_session()
        with self.assertRaises(ValueError):
            session.send('P', b'x' * 1048576)
        session.writer.write = lambda data: 0
        with self.assertRaises(EOFError):
            session.send('F')

    def test_parameter_cache_returns_fresh_trees_and_tracks_updates_and_access(self):
        calls = []
        def decode(context, payload):
            calls.append(payload)
            return SimpleNamespace(items=[int(payload)])
        ctx = ComponentContext.__new__(ComponentContext)
        ctx.config = {'parameter_bindings': {'p': {'access': 'read'}}}
        ctx.session = SimpleNamespace(parameter_values={'p': ('catalogue', b'1')})
        ctx.codec = SimpleNamespace(api=SimpleNamespace(decode=decode))
        ctx._parameter_codecs, ctx.parameter_types = {'p': None}, {'p': SimpleNamespace}
        ctx._parameter_cache = {}
        ctx.parameter('p').items.append(99)
        self.assertEqual(ctx.parameter('p').items, [1])
        self.assertEqual(calls, [b'1'])
        ctx.session.parameter_values['p'] = ('catalogue', b'2')
        self.assertEqual(ctx.parameter('p').items, [2])
        self.assertEqual(calls, [b'1', b'2'])
        ctx.config['parameter_bindings']['p']['access'] = 'write'
        with self.assertRaises(ValueError):
            ctx.parameter('p')


if __name__ == '__main__':
    unittest.main()
