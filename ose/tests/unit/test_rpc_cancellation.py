"""RPC completion must never consume cancellation of its caller."""
import asyncio
from types import SimpleNamespace
import unittest

from ose.transport.tcp import Client


class RequestCancellationTests(unittest.IsolatedAsyncioTestCase):
    def client(self, send):
        client = Client.__new__(Client)
        client.tcp = SimpleNamespace(send=send)
        client.pending, client.sequence = {}, 0
        client.request_timeout = 1
        return client

    async def test_simultaneous_reply_and_cancellation_propagates_cancellation(self):
        async def send(header, payload):
            client.pending[header['request']].set_result(({'op': 'result'}, b''))
            asyncio.get_running_loop().call_soon(request.cancel)

        client = self.client(send)
        request = asyncio.create_task(client.request('status'))
        with self.assertRaises(asyncio.CancelledError):
            await request
        self.assertEqual(client.pending, {})

    async def test_timeout_cancels_exchange_and_clears_pending_request(self):
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def send(header, payload):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        client = self.client(send)
        with self.assertRaises(asyncio.TimeoutError):
            await client.request('status', timeout=.02)
        self.assertTrue(entered.is_set())
        self.assertTrue(stopped.is_set())
        self.assertEqual(client.pending, {})

    async def test_reply_and_transport_error_keep_their_original_result(self):
        async def send(header, payload):
            client.pending[header['request']].set_result(({'op': 'result'}, b'reply'))

        client = self.client(send)
        self.assertEqual(await client.request('status'), ({'op': 'result'}, b'reply'))
        self.assertEqual(client.pending, {})

        async def broken(header, payload):
            raise ConnectionError('disconnected')

        client.tcp.send = broken
        with self.assertRaisesRegex(ConnectionError, 'disconnected'):
            await client.request('status')
        self.assertEqual(client.pending, {})
