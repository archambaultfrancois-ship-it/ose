"""Existing parameter RPCs, separate command and long-poll connections."""
import asyncio
import os
from pathlib import Path
import time
import uuid
from ose.transport.tcp import RemoteError
from ose_ws.schema.adapter import SchemaAdapter
from ose_ws.util import APIError, dumps, loads, opaque, require, utc


class ParameterManager:
    def __init__(self, config, connection, collector):
        self.config, self.connection, self.collector = config, connection, collector
        self.client = None
        self.lock = asyncio.Lock()
        self.inventory, self.schemas, self.changes = {}, {}, {}
        self.error = "Parameter access has not been initialized."
        self.epoch = None
        self.refreshed = 0
        self.pollers = []
        self.task = None
        self.imports = {}
        self.stale_paths = set()
        self.presentation = {}
        path = config["parameters"].get("presentation")
        if path:
            self.presentation = loads(Path(path).read_bytes())
            if not isinstance(self.presentation, dict):
                raise ValueError("Parameter presentation must be a JSON object keyed by root type.")

    async def connect(self):
        settings = self.config["parameters"]
        token = os.environ.get(settings["token_env"])
        if not token and self.connection.context is None:
            raise APIError(503, "PARAMETERS_NOT_CONFIGURED", "Set the configured OSE parameter token environment variable and declare the client rights in the OSE deployment.")
        client = await self.connection.connect()
        try:
            await client.request("parameters_open", client=settings["client"], token=token, timeout=3)
        except BaseException:
            await client.close()
            raise
        return client

    async def command(self):
        current = self.collector.snapshot.get("epoch")
        if self.client and self.epoch != current:
            await self.client.close()
            self.client = None
            self.schemas.clear()
        if self.client is None:
            self.client = await self.connect()
            self.epoch = current
        return self.client

    async def reset(self):
        if self.client:
            await self.client.close()
            self.client = None

    async def refresh(self):
        async with self.lock:
            try:
                client = await self.command()
                result, _ = await client.request("parameters_list", timeout=3)
                require(len(result["blocks"]) <= 4096, "Parameter inventory exceeds 4096 blocks.", 413)
                self.inventory = {opaque("block", path): path for path in result["blocks"]}
                self.changes = {identity: value for identity, value in self.changes.items() if identity in self.inventory}
                self.stale_paths.intersection_update(self.inventory.values())
                self.refreshed, self.error = time.monotonic(), None
            except Exception as error:
                self.error = str(error)[:500]
                await self.reset()
                raise

    async def path(self, identity):
        if time.monotonic() - self.refreshed > 5 or identity not in self.inventory:
            await self.refresh()
        require(identity in self.inventory, "Parameter block not found.", 404)
        return self.inventory[identity]

    async def describe_unlocked(self, client, path):
        result, catalogue = await client.request("parameters_describe", path=path, timeout=3)
        root = result["root"]
        schema = await asyncio.to_thread(SchemaAdapter, catalogue.decode(), root, self.presentation.get(root, {}))
        self.schemas[path] = schema
        return schema

    async def schema(self, path):
        async with self.lock:
            try:
                return await self.describe_unlocked(await self.command(), path)
            except (OSError, ConnectionError, asyncio.TimeoutError):
                await self.reset()
                raise

    async def get(self, path, *, mark_observed=True):
        async with self.lock:
            try:
                client = await self.command()
                schema = await self.describe_unlocked(client, path)
                _, payload = await client.request("parameters_get", path=path, timeout=3)
                if mark_observed:
                    self.stale_paths.discard(path)
                return {"value": await asyncio.to_thread(schema.read, payload), "root": schema.root,
                        "schema_digest": schema.digest, "observed_at": utc(), "application_epoch": self.epoch}
            except (OSError, ConnectionError, asyncio.TimeoutError):
                await self.reset()
                raise

    async def validate(self, path, body, *, ose_format=False):
        schema = await self.schema(path)
        require(body.get("schema_digest", schema.digest) == schema.digest, "The SDL contract changed; reload the block.", 409, "SCHEMA_CHANGED")
        preview, _ = await asyncio.to_thread(schema.validate, body["value"], ose_format=ose_format)
        return preview

    async def set(self, path, body):
        require(self.collector.error is None, "OSE is unavailable; reload before applying parameters.", 503)
        require(path not in self.stale_paths, "Parameter notifications were interrupted; reload this block before applying.", 503, "PARAMETER_STALE")
        async with self.lock:
            try:
                client = await self.command()
                schema = await self.describe_unlocked(client, path)
                require(body.get("schema_digest") == schema.digest, "The SDL contract changed; validate again.", 409, "SCHEMA_CHANGED")
                preview, payload = await asyncio.to_thread(schema.validate, body["value"])
                require(body.get("preview_hash") == preview["preview_hash"], "Validate this exact value before applying it.", 409, "PREVIEW_REQUIRED")
                await client.request("parameters_set", payload, path=path, timeout=3)
                return {"result": "stored", "path": path, "observed_at": utc()}
            except BaseException:
                await self.reset()
                raise

    async def poll(self, paths):
        client = None
        try:
            client = await self.connect()
            await client.request("parameters_subscribe", paths=paths, timeout=3)
            self.error = None
            while True:
                result, _ = await client.request("parameters_next", timeout=3)
                if not result.get("empty"):
                    identity = opaque("block", result["path"])
                    self.changes[identity] = self.changes.get(identity, 0) + 1
        finally:
            self.stale_paths.update(paths)
            self.error = "Parameter notifications were interrupted; visible blocks must be reloaded."
            if client:
                await client.close()

    async def run(self):
        while True:
            try:
                await self.refresh()
                paths = sorted(self.inventory.values())
                # Probe reads without sending writes. Write-only blocks remain in the inventory.
                readable = []
                for path in paths:
                    try:
                        await self.get(path, mark_observed=False)
                        readable.append(path)
                    except RemoteError:
                        continue
                self.pollers = [asyncio.create_task(self.poll(readable[i:i + 256])) for i in range(0, len(readable), 256)]
                epoch = self.collector.snapshot.get("epoch")
                while self.collector.snapshot.get("epoch") == epoch and self.collector.error is None and not any(t.done() for t in self.pollers):
                    self.imports = {key: plan for key, plan in self.imports.items() if plan["until"] > time.monotonic()}
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.error = str(error)[:500]
            finally:
                for task in self.pollers:
                    task.cancel()
                await asyncio.gather(*self.pollers, return_exceptions=True)
                self.pollers = []
            await asyncio.sleep(3)

    async def preview_import(self, session, document):
        require(isinstance(document, dict) and bool(document), "An import must map exact paths to parameter values.")
        self.imports = {key: plan for key, plan in self.imports.items() if plan["until"] > time.monotonic()}
        require(len(self.imports) < self.config["limits"]["max_drafts"], "Import plan quota reached.", 429)
        blocks = []
        for path, value in document.items():
            session.check("parameters.write", path)
            session.check("parameters.read", path)
            preview = await self.validate(path, {"value": value}, ose_format=True)
            current = await self.get(path)
            blocks.append({"block_id": opaque("block", path), "path": path, "before": current["value"], **preview})
        identity = uuid.uuid4().hex
        size = len(dumps(blocks).encode())
        require(sum(plan.get("bytes", 0) for plan in self.imports.values()) + size <= 64 * 1024**2,
                "The shared import preview budget is exhausted.", 429, "IMPORT_QUOTA")
        self.imports[identity] = {"actor": session.actor, "until": time.monotonic() + 600, "blocks": blocks,
                                  "epoch": self.collector.snapshot.get("epoch"), "bytes": size}
        return {"id": identity, "expires_in_s": 600, "blocks": blocks}

    def import_plan(self, session, identity):
        plan = self.imports.get(identity)
        require(plan is not None and plan["actor"] == session.actor and plan["until"] > time.monotonic(),
                "Import plan is missing or expired.", 410, "IMPORT_EXPIRED")
        require(plan["epoch"] == self.collector.snapshot.get("epoch"), "OSE restarted; preview the import again.", 409)
        return plan

    async def apply_import(self, session, plan):
        results, failed = [], False
        for block in plan["blocks"]:
            item = {"block_id": block["block_id"], "path": block["path"], "result": "not_attempted"}
            if not failed:
                try:
                    session.check("parameters.write", block["path"])
                    item.update(await self.set(block["path"], block))
                except (APIError, RemoteError, ValueError) as error:
                    item.update(result="refused", error=str(error)[:500])
                    failed = True
                except Exception:
                    item.update(result="uncertain", error="No acknowledgement received. This block was not retried.")
                    failed = True
            results.append(item)
        return {"blocks": results, "result": "partial" if failed else "stored"}

    async def start(self):
        self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.reset()
