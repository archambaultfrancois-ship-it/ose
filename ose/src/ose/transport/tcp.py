import asyncio
import json
import struct
from ose.transport.protocol import encode, decode


class RemoteError(ValueError):
    def __init__(self, message, code="invalid_request"):
        self.code = code
        super().__init__(message)


class _FrameReader(asyncio.StreamReader):
    """Extract frames from StreamReader's buffer without copying whole packets.

    Keep access to asyncio's buffer/wait/flow-control internals in this subclass.
    StreamReaderProtocol and StreamWriter retain their standard lifecycle and
    write backpressure, including TLS.
    """
    def _incomplete(self, expected, offset=0):
        partial = bytes(self._buffer[offset:])
        self._buffer.clear()
        self._maybe_resume_transport()
        raise asyncio.IncompleteReadError(partial, expected)

    async def read_frame(self, limit):
        if self._exception is not None:
            raise self._exception
        while len(self._buffer) < 4:
            if self._eof:
                self._incomplete(4)
            await self._wait_for_data('read_frame')
        size = struct.unpack_from('>I', self._buffer)[0]
        if not 7 <= size <= limit:
            del self._buffer[:4]
            self._maybe_resume_transport()
            raise ValueError('invalid TCP frame length')
        end = 4 + size
        while len(self._buffer) < end:
            if self._eof:
                self._incomplete(size, 4)
            await self._wait_for_data('read_frame')
        try:
            return decode(self._buffer, limit, start=4, end=end)
        finally:
            del self._buffer[:end]
            self._maybe_resume_transport()


async def open_connection(host, port, **options):
    loop = asyncio.get_running_loop()
    reader = _FrameReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.create_connection(lambda: protocol, host, port, **options)
    return reader, asyncio.StreamWriter(transport, protocol, reader, loop)


async def start_server(callback, host, port, **options):
    loop = asyncio.get_running_loop()
    def factory():
        return asyncio.StreamReaderProtocol(_FrameReader(), callback)
    return await loop.create_server(factory, host, port, **options)


class TCP:
    def __init__(self, reader, writer, limit=1024 * 1024):
        self.reader, self.writer, self.limit = reader, writer, limit
        self.lock = asyncio.Lock()

    async def send(self, header, payload=b""):
        packet = encode(header, payload, self.limit)
        async with self.lock:
            self.writer.write(struct.pack(">I", len(packet)) + packet)
            await self.writer.drain()

    async def receive(self):
        if isinstance(self.reader, _FrameReader):
            return await self.reader.read_frame(self.limit)
        size = struct.unpack(">I", await self.reader.readexactly(4))[0]
        if not 7 <= size <= self.limit:
            raise ValueError("invalid TCP frame length")
        return decode(await self.reader.readexactly(size), self.limit)

    async def close(self):
        self.writer.close()
        try:
            await asyncio.wait_for(self.writer.wait_closed(), .5)
        except asyncio.TimeoutError:
            # A peer that no longer reads must not keep shutdown flushing forever.
            self.writer.transport.abort()
        except ConnectionError:
            pass  # A peer reset is already a completed close.


class Client:
    def __init__(self, tcp, on_data=None, request_timeout=15):
        self.tcp, self.on_data = tcp, on_data
        self.request_timeout = request_timeout
        self.on_event = None
        self.pending, self.sequence = {}, 0
        self.task = asyncio.create_task(self._read())

    @classmethod
    async def connect(cls, host, port, on_data=None, limit=1024 * 1024, request_timeout=15):
        reader, writer = await open_connection(host, port)
        return cls(TCP(reader, writer, limit), on_data, request_timeout)

    async def request(self, op, payload=b"", *, timeout=None, **arguments):
        self.sequence += 1
        identity = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            async def exchange():
                await self.tcp.send({"op": op, "request": identity, **arguments}, payload)
                return await future
            header, body = await asyncio.wait_for(exchange(), self.request_timeout if timeout is None else timeout)
            if "error" in header:
                raise RemoteError(header["error"], header.get("error_code", "invalid_request"))
            if header.get("content") == "json":
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError("invalid JSON control result")
                header = {**result, **header}
                body = b""
            return header, body
        finally:
            self.pending.pop(identity, None)

    async def _read(self):
        try:
            while True:
                header, body = await self.tcp.receive()
                if header["op"] == "result":
                    future = self.pending.get(header["request"])
                    if future and not future.done():
                        future.set_result((header, body))
                elif header["op"] == "data" and self.on_data:
                    await self.on_data(header, body)
                elif header["op"] == "event" and self.on_event:
                    self.on_event(header, body)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError) as error:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError(str(error)))
            raise

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await self.tcp.close()
