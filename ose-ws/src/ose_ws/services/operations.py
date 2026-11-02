"""At-most-once dispatch with explicit uncertain outcomes after transport loss."""
import asyncio
import uuid
from ose.transport.tcp import RemoteError
from ose_ws.util import APIError


class Operations:
    def __init__(self, store):
        self.store = store
        self.tasks = {}
        self.volatile = {}

    async def run(self, session, target, key, body, effect, *, background=False, emergency_stop=False, replay_only=False):
        try:
            operation, created = await self.store.intention(session.actor, target, key, body, not replay_only)
        except APIError as error:
            if not emergency_stop or error.code not in ("STORAGE_UNAVAILABLE", "OPERATION_QUOTA", "UNRESOLVED_OPERATION_QUOTA"):
                raise
            identity = uuid.uuid4().hex
            operation = {"id": identity, "actor": session.actor, "target": target, "status": "pending", "result": None,
                         "durable": False, "warning": "Emergency stop: metadata could not be persisted."}
            self.volatile[identity] = operation
            created = True
        if not created:
            return operation
        identity = operation["id"]
        async def dispatch():
            status, result = "complete", None
            try:
                result = await effect()
            except RemoteError as error:
                status, result = "failed", {"error": str(error)[:500]}
            except APIError as error:
                status, result = "failed", {"error": error.message, "code": error.code, "details": error.details}
            except (ValueError, ImportError) as error:
                status, result = "failed", {"error": str(error)[:500]}
            except BaseException as error:
                status, result = "uncertain", {"error": "The result could not be confirmed; read the current state. No automatic retry was sent."}
                if not isinstance(error, (Exception, asyncio.CancelledError)):
                    raise
            operation.update(status=status, result=result)
            try:
                if identity not in self.volatile:
                    await self.store.finish(identity, status, result)
                    operation.update(await self.store.operation(identity))
            except APIError:
                operation.update(status="uncertain", durable=False,
                                 warning="The operation result could not be persisted; check the target state.")
                self.volatile[identity] = operation
            return operation
        task = asyncio.create_task(dispatch())
        self.tasks[identity] = task
        task.add_done_callback(lambda done: self.tasks.pop(identity, None))
        if background:
            return operation
        return await asyncio.shield(task)

    async def get(self, identity):
        return self.volatile.get(identity) or await self.store.operation(identity)

    async def close(self, timeout):
        if self.tasks:
            _, pending = await asyncio.wait(list(self.tasks.values()), timeout=timeout)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
