"""Read the OSE log service over a separate, reusable control connection."""
import asyncio


class LogReader:
    def __init__(self, connection):
        self.connection, self.client = connection, None
        self.lock = asyncio.Lock()

    async def query(self, filters):
        async with self.lock:
            try:
                if self.client is None:
                    self.client = await self.connection.connect()
                result, _ = await self.client.request('logs', filters=filters, timeout=3)
                return {key: result[key] for key in ('epoch', 'items', 'cursor', 'more', 'truncated', 'evicted', 'retained')}
            except BaseException:
                if self.client is not None:
                    await self.client.close()
                    self.client = None
                raise

    async def close(self):
        if self.client is not None:
            await self.client.close()
            self.client = None
