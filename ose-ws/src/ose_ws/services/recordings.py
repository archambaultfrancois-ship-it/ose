"""Independent Recorder instances with durable ownership and protected artifacts."""
import asyncio
import errno
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import uuid
import weakref
from ose.recording.recorder import Recorder
from ose.recording.selection import resolve
from ose_ws.schema.adapter import SchemaAdapter
from ose_ws.util import APIError, loads, opaque, require, utc


ACTIVE = ("STARTING", "RECORDING", "STOPPING")
DELETABLE = ("DRAFT", "STOPPED", "FAILED", "INTERRUPTED")


class RecordingManager:
    def __init__(self, config, connection, store):
        self.config, self.connection, self.store = config, connection, store
        self.objects, self.recorders = {}, {}
        self.locks = weakref.WeakValueDictionary()
        self.points, self.point_ids = {}, {}
        self.discovery = None
        self.discovery_lock = asyncio.Lock()
        self.admission_lock = asyncio.Lock()
        self.validation_lock = asyncio.Semaphore(1)
        self.refreshed = 0
        self.task = None
        self.error = None
        self.event = 0

    async def open(self):
        self.objects = {c["id"]: c for c in await self.store.captures()}
        self.task = asyncio.create_task(self.monitor())

    def new_recorder(self, settings=None):
        settings = settings or {}
        host, port = self.connection.address()
        return Recorder(host, port, context=self.connection.context,
                        distributed=self.connection.distributed,
                        capacity=settings.get("capacity", 256), byte_budget=settings.get("byte_budget", 8 * 1024**2),
                        label=settings.get("label", "recorder"), max_size=settings.get("max_size"), cycle=settings.get("cycle"))

    async def refresh(self, force=False):
        async with self.discovery_lock:
            if not force and time.monotonic() - self.refreshed < 3:
                return self.points
            try:
                if self.discovery:
                    await self.discovery.close()
                self.discovery = self.new_recorder()
                await self.discovery.connect()
                self.points = self.discovery.points
                for topic, point in self.points.items():
                    component, _, name = point["topic"].partition(".")
                    point.setdefault("component", component)
                    point.setdefault("name", name)
                self.point_ids = {opaque("point", topic): topic for topic in self.points}
                self.refreshed, self.error = time.monotonic(), None
            except Exception as error:
                self.error = str(error)[:500]
                if self.discovery:
                    await self.discovery.close()
                self.discovery = None
                raise
        return self.points

    async def describe(self, identity):
        await self.refresh()
        require(identity in self.point_ids, "Recording point not found.", 404)
        async with self.discovery_lock:
            return await self.discovery.describe(self.point_ids[identity])

    @staticmethod
    def allowed(session, capture, permission):
        if session.allows("admin"):
            return True
        if capture["owner"] != session.actor:
            return permission in ("recordings.read", "recordings.stop", "recordings.download") and capture["id"] in session.permissions.get(permission, [])
        if permission == "recordings.create":
            return all(session.allows(permission, point["topic"]) for point in capture["selection"])
        return session.allows(permission, capture["id"])

    def get(self, session, identity, permission="recordings.read"):
        capture = self.objects.get(identity)
        require(capture is not None and self.allowed(session, capture, permission), "Capture not found or not authorized.", 404)
        return capture

    def public(self, capture, session=None):
        result = dict(capture)
        result["status"] = {k: v for k, v in capture.get("status", {}).items() if k not in ("file", "base_file")}
        if session is not None:
            result["permissions"] = {"delete": self.allowed(session, capture, "recordings.delete")}
        return result

    def settings(self, body):
        require(isinstance(body, dict), "Capture configuration must be an object.")
        label = body.get("label", "recorder")
        require(isinstance(label, str) and 1 <= len(label.encode()) <= 200 and all(c.isprintable() for c in label), "Invalid capture label.")
        require(body.get("format", "pcapng") in ("pcapng", "mcap"), "Choose pcapng or mcap.")
        for name, low, high, default in (("capacity", 1, 4096, 256), ("byte_budget", 1024, 64 * 1024**2, 8 * 1024**2)):
            value = body.get(name, default)
            require(type(value) is int and low <= value <= high, f"Invalid {name}.")
        for name in ("max_size", "cycle"):
            require(body.get(name) is None or type(body[name]) is int and body[name] > 0, f"Invalid {name}.")
        require(body.get("cycle") is None or body.get("max_size") is not None, "Cyclic retention requires a rotation size.")
        return {"label": label, "format": body.get("format", "pcapng"), "capacity": body.get("capacity", 256),
                "byte_budget": body.get("byte_budget", 8 * 1024**2), "max_size": body.get("max_size"), "cycle": body.get("cycle")}

    async def selection(self, session, body):
        await self.refresh()
        patterns = body.get("selection", [])
        require(isinstance(patterns, list) and patterns and all(isinstance(p, str) for p in patterns), "Select recording points, types or globs.")
        kind = body.get("kind") or None
        require(kind in (None, "input", "output", "internal", "external"), "Invalid recording point kind.")
        topics = set()
        for pattern in patterns:
            if pattern in self.point_ids:
                topics.add(self.point_ids[pattern])
            else:
                topics.update(resolve(self.points, [pattern], kind=kind))
        topics = sorted(topics)
        filters = body.get("filters", {})
        require(isinstance(filters, dict) and all(isinstance(value, str) for value in filters.values()), "Invalid point filters.")
        require(all(len(value) <= 16384 for value in filters.values()), "A source filter exceeds 16384 characters.")
        resolved_filters = {}
        for selector, expression in filters.items():
            matches = ([self.point_ids[selector]] if selector in self.point_ids else
                       [key for key in topics if self.points[key]["topic"] == selector])
            require(matches and all(key in topics for key in matches), "Invalid point filters.")
            for key in matches:
                require(key not in resolved_filters or resolved_filters[key] == expression, "Conflicting point filters.")
                resolved_filters[key] = expression
        filters = resolved_filters
        result = []
        for topic in topics:
            point = self.points[topic]
            session.check("recordings.create", point["topic"])
            require(point["available"], f"Point unavailable: {point['topic']}", 409)
            if filters.get(topic):
                async with self.discovery_lock:
                    description = await self.discovery.describe(topic)
                def validate_filter():
                    from ose.recording.filters import prepare_filter
                    predicate = prepare_filter(description, filters[topic])
                    predicate.close()
                await asyncio.to_thread(validate_filter)
            result.append({"topic": point["topic"], "point_key": topic, "point_id": opaque("point", topic),
                           "kind": point.get("kind", "output"), "generation": point["generation"],
                           "root": point["root"], "filter": filters.get(topic) or None})
        return result

    async def create(self, session, body):
        require(sum(c["state"] == "DRAFT" for c in self.objects.values()) < self.config["limits"]["max_drafts"], "Draft quota reached.", 429)
        require(len(self.objects) < 10000, "Capture metadata quota reached; delete unused drafts or finished captures.", 429)
        settings = self.settings(body)
        selection = await self.selection(session, body)
        capture = {"id": uuid.uuid4().hex, "owner": session.actor, "state": "DRAFT", "revision": 1,
                   "created_at": utc(), "settings": settings, "selection": selection, "status": {}}
        await self.store.save_capture(capture)
        self.objects[capture["id"]] = capture
        self.event += 1
        return self.public(capture)

    async def patch(self, session, identity, body):
        async with self.locks.setdefault(identity, asyncio.Lock()):
            capture = self.get(session, identity, "recordings.create")
            require(capture["state"] == "DRAFT", "Only a draft can be changed.", 409)
            require(body.get("revision") == capture["revision"], "The draft changed; reload it.", 409)
            replacement = dict(capture, settings=self.settings(body), selection=await self.selection(session, body), revision=capture["revision"] + 1)
            await self.store.save_capture(replacement)
            self.objects[identity] = replacement
            self.event += 1
            return self.public(replacement)

    async def persist(self, capture):
        capture["updated_at"] = utc()
        self.event += 1
        try:
            await self.store.save_capture(capture)
        except APIError:
            capture["storage_warning"] = "Capture state could not be persisted. Stop remains available."

    def delete_files(self, identity):
        parent = os.open(self.store.root / "captures", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directory = None
        try:
            try:
                directory = os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                return
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise APIError(409, "CAPTURE_FILES_CHANGED", "The capture directory was replaced; files were kept.") from None
                raise
            names = os.listdir(directory)
            # Check every entry before unlinking anything. Never traverse directories
            # or links, and preserve files not created by the capture writer.
            for name in names:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) and re.fullmatch(r"capture(?:_[0-9]{3,})?\.(?:pcapng|mcap)", name),
                        "The capture directory contains unexpected entries; files were kept. Remove the capture while keeping files or inspect its directory.",
                        409, "CAPTURE_FILES_CHANGED")
            for name in names:
                os.unlink(name, dir_fd=directory)
            os.rmdir(identity, dir_fd=parent)
        finally:
            if directory is not None:
                os.close(directory)
            os.close(parent)

    async def delete(self, session, identity, delete_files):
        async with self.locks.setdefault(identity, asyncio.Lock()):
            capture = self.get(session, identity, "recordings.delete")
            require(capture["state"] in DELETABLE and identity not in self.recorders,
                    "Stop and finalize the capture before deleting it.", 409, "CAPTURE_ACTIVE")
            def remove():
                if delete_files:
                    self.delete_files(identity)
                with self.store.db:
                    self.store.db.execute("DELETE FROM files WHERE capture=?", (identity,))
                    self.store.db.execute("DELETE FROM captures WHERE id=?", (identity,))
            await self.store.call(remove)
            self.objects.pop(identity)
            self.event += 1
            return {"recording_id": identity, "deleted": True, "files_deleted": delete_files,
                    "retained_directory": None if delete_files else f"captures/{identity}/"}

    async def start(self, session, identity):
        async with self.locks.setdefault(identity, asyncio.Lock()):
            capture = self.get(session, identity, "recordings.create")
            require(capture["state"] == "DRAFT", "A capture can be started only once.", 409)
            async with self.admission_lock:
                active = [c for c in self.objects.values() if c["state"] in ACTIVE]
                require(len(active) < self.config["recording"]["max_active"], "Active capture quota reached.", 429)
                require(sum(c["owner"] == session.actor for c in active) < self.config["recording"]["per_user"], "Your active capture quota is reached.", 429)
                free = await asyncio.to_thread(lambda: shutil.disk_usage(self.store.root).free)
                require(free >= self.config["recording"]["min_free_disk_mib"] * 1024**2, "Not enough free disk space to start.", 409, "DISK_SPACE")
                capture["state"] = "STARTING"
                # No remote taps are opened if this transition cannot be persisted.
                await self.store.save_capture(capture)
            recorder = None
            try:
                recorder = self.new_recorder(capture["settings"])
                await recorder.connect()
                for selected in capture["selection"]:
                    session.check("recordings.create", selected["topic"])
                    point_key = selected.get("point_key", selected["topic"])
                    desc = await recorder.describe(point_key)
                    require(desc["generation"] == selected["generation"] and desc["root"] == selected["root"],
                            "A selected source changed; create a new draft.", 409, "SOURCE_CHANGED")
                    require("kind" not in selected or desc.get("kind", "output") == selected["kind"],
                            "A selected point changed kind; create a new draft.", 409, "SOURCE_CHANGED")
                    recorder.selected[point_key] = selected["filter"]
                directory = self.store.root / "captures" / identity
                directory.mkdir(mode=0o700)
                await recorder.start(directory / ("capture." + capture["settings"]["format"]), format=capture["settings"]["format"])
                self.recorders[identity] = recorder
                capture["state"] = "RECORDING"
                capture["started_at"] = utc()
                await self.persist(capture)
                return {"recording_id": identity, "state": "RECORDING"}
            except BaseException as error:
                capture["state"], capture["error"] = "FAILED", str(error)[:500] or "Capture start was interrupted."
                if recorder:
                    await recorder.close()
                await self.persist(capture)
                raise

    async def stop(self, session, identity):
        async with self.locks.setdefault(identity, asyncio.Lock()):
            capture = self.get(session, identity, "recordings.stop")
            if capture["state"] in ACTIVE:
                capture["state"] = "STOPPING"
                await self.persist(capture)
                recorder = self.recorders.get(identity)
                if recorder:
                    try:
                        await asyncio.wait_for(recorder.stop(), self.config["limits"]["shutdown_s"])
                    except asyncio.TimeoutError:
                        capture["error"] = "Drain deadline exceeded; artifact integrity is unknown."
                        capture["state"] = "INTERRUPTED"
                    await self.sync(identity, recorder)
            return {"recording_id": identity, "state": capture["state"]}

    async def sync(self, identity, recorder):
        capture = self.objects[identity]
        status = recorder.status()
        status["file_size_bytes"] = await asyncio.to_thread(
            self.file_size, identity, Path(status.get("file") or "").name)
        capture["status"] = status
        if status["state"] in ("failed", "stopped"):
            if capture["state"] != "INTERRUPTED":
                capture["state"] = status["state"].upper()
            capture["error"] = status.get("error") or capture.get("error")
            capture["ended_at"] = utc()
            await recorder.close()
            self.recorders.pop(identity, None)
            await self.persist(capture)

    async def monitor(self):
        while True:
            await asyncio.sleep(1)
            try:
                free = await asyncio.to_thread(lambda: shutil.disk_usage(self.store.root).free)
                for identity, recorder in list(self.recorders.items()):
                    async with self.locks.setdefault(identity, asyncio.Lock()):
                        if self.recorders.get(identity) is not recorder:
                            continue
                        if free < self.config["recording"]["min_free_disk_mib"] * 1024**2 and recorder.capture and not recorder.capture.task.done():
                            self.objects[identity]["error"] = "Capture stopped because free disk space reached the configured threshold."
                            await asyncio.wait_for(recorder.stop(), self.config["limits"]["shutdown_s"])
                        await self.sync(identity, recorder)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.error = str(error)[:500]

    def file_size(self, identity, name):
        """Sample the current file on disk, without following replaced links."""
        if not re.fullmatch(r"capture(?:_[0-9]{3,})?\.(?:pcapng|mcap)", name):
            return None
        directory = None
        try:
            directory = self.directory_fd(identity)
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            return info.st_size if stat.S_ISREG(info.st_mode) else None
        except OSError:
            return None
        finally:
            if directory is not None:
                os.close(directory)

    def directory_fd(self, identity):
        captures = os.open(self.store.root / "captures", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            return os.open(identity, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=captures)
        finally:
            os.close(captures)

    async def files(self, session, identity):
        async with self.locks.setdefault(identity, asyncio.Lock()):
            return await self._files(session, identity)

    async def _files(self, session, identity):
        capture = self.get(session, identity, "recordings.read")
        require(capture["state"] not in ACTIVE + ("DRAFT",), "Files become available after the capture ends.", 409)
        def discover():
            directory = None
            try:
                directory = self.directory_fd(identity)
                found = {}
                for name in os.listdir(directory):
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or not re.fullmatch(r"capture(?:_[0-9]{3,})?\.(?:pcapng|mcap)", name):
                        continue
                    file_id = opaque("file", identity, name, info.st_dev, info.st_ino)
                    found[file_id] = (name, info)
                with self.store.db:
                    self.store.db.execute("UPDATE files SET expired=1 WHERE capture=?", (identity,))
                    for file_id, (name, info) in found.items():
                        self.store.db.execute("INSERT INTO files(id,capture,name,device,inode) VALUES (?,?,?,?,?) "
                                              "ON CONFLICT(id) DO UPDATE SET expired=0", (file_id, identity, name, info.st_dev, info.st_ino))
                return [dict(row) | {"size": found[row["id"]][1].st_size if row["id"] in found else None,
                                     "capture_state": capture["state"]} for row in self.store.db.execute("SELECT * FROM files WHERE capture=?", (identity,))]
            except FileNotFoundError:
                return []
            finally:
                if directory is not None:
                    os.close(directory)
        return await self.store.call(discover)

    async def open_file(self, session, identity):
        row = await self.store.call(lambda: self.store.db.execute("SELECT * FROM files WHERE id=?", (identity,)).fetchone())
        require(row is not None, "File not found.", 404)
        capture = self.get(session, row["capture"], "recordings.download")
        require(capture["state"] not in ACTIVE, "A live capture cannot be downloaded.", 409)
        require(not row["expired"], "This file expired under cyclic retention.", 410)
        directory = self.directory_fd(row["capture"])
        try:
            fd = os.open(row["name"], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        finally:
            os.close(directory)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (row["device"], row["inode"]):
            os.close(fd)
            raise APIError(409, "FILE_CHANGED", "The artifact was replaced outside the console.")
        return fd, row["name"]

    async def validate_file(self, session, identity):
        async with self.validation_lock:
            fd, _ = await self.open_file(session, identity)
            worker = None
            try:
                try:
                    worker = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "ose_ws.storage.validate", f"/proc/self/fd/{fd}", pass_fds=(fd,),
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                    output, _ = await asyncio.wait_for(worker.communicate(), self.config["limits"]["validation_s"])
                    require(worker.returncode == 0, "The validation worker could not complete.", 503, "VALIDATION_FAILED")
                    result = loads(output, max_bytes=4096)
                    integrity = result["integrity"]
                except asyncio.TimeoutError:
                    raise APIError(504, "VALIDATION_TIMEOUT", "Artifact validation exceeded its deadline; integrity remains unknown.") from None
                def save():
                    with self.store.db:
                        self.store.db.execute("UPDATE files SET integrity=? WHERE id=?", (integrity, identity))
                await self.store.call(save)
                return result
            finally:
                if worker and worker.returncode is None:
                    worker.terminate()
                    try:
                        await asyncio.wait_for(worker.wait(), 2)
                    except asyncio.TimeoutError:
                        worker.kill()
                        await worker.wait()
                os.close(fd)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for identity, recorder in list(self.recorders.items()):
            try:
                await asyncio.wait_for(recorder.close(), self.config["limits"]["shutdown_s"])
                await self.sync(identity, recorder)
            except Exception:
                self.objects[identity]["state"] = "INTERRUPTED"
                await self.persist(self.objects[identity])
        if self.discovery:
            await self.discovery.close()
