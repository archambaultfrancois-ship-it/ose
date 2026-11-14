"""Simulation commands use independent connections so pause can interrupt an advance."""
import asyncio
import uuid
from ose_ws.util import require


class SimulationManager:
    def __init__(self, connection, collector):
        self.connection, self.collector = connection, collector

    async def execute(self, data):
        require(set(data) <= {'epoch', 'action', 'count', 'time_ns', 'components', 'enabled', 'persistent'}, 'Unknown simulation command field.')
        require(isinstance(data.get('epoch'), str), 'An execution epoch is required.')
        client = await self.connection.connect()
        try:
            operation, _ = await client.request('simulation_command', **data, command_id=uuid.uuid4().hex, timeout=5)
            while operation['status'] == 'running':
                await asyncio.sleep(.1)
                operation, _ = await client.request('simulation_operation', id=operation['id'], timeout=5)
            require(operation['status'] != 'failed', operation.get('error') or 'Simulation command failed.', 422)
            return {k: v for k, v in operation.items() if k not in ('op', 'request', 'content')}
        finally:
            await client.close()
