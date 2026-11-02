"""One SQLite owner, one filesystem lock, durable intentions before effects."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import time
import uuid
from ose_ws.util import APIError, dumps, loads, timestamp, utc


class StateStore:
    def __init__(self, config):
        self.config = config
        self.root = Path(config["state_dir"])
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ose-ws-state")
        self.failed = False
        self.lock = None
        self.db = None

    async def call(self, function, *args):
        try:
            return await asyncio.get_running_loop().run_in_executor(self.executor, function, *args)
        except (sqlite3.Error, OSError):
            self.failed = True
            raise APIError(503, "STORAGE_UNAVAILABLE", "Local metadata storage is unavailable.") from None

    async def open(self):
        await self.call(self._open)

    def _open(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        fd = os.open(self.root / "server.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(fd, "r+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner = self.lock.readline(32).strip()
            process = f" (PID {owner})" if owner.isascii() and owner.isdecimal() else ""
            raise APIError(409, "STATE_IN_USE", f"Another ose-ws process{process} is already using this state directory. Stop that console before starting another instance.") from None
        self.lock.seek(0)
        self.lock.truncate()
        self.lock.write(f"{os.getpid()}\n")
        self.lock.flush()
        database = self.root / "state.sqlite3"
        if database.is_symlink():
            raise OSError("Metadata database cannot be a symbolic link.")
        self.db = sqlite3.connect(database, timeout=3)
        os.chmod(database, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise ValueError("The metadata schema is newer than this server.")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS operations (
            id TEXT PRIMARY KEY, actor TEXT NOT NULL, target TEXT NOT NULL, key TEXT NOT NULL,
            body_hash TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created REAL NOT NULL,
            expires REAL, UNIQUE(actor,target,key));
          CREATE TABLE IF NOT EXISTS captures (id TEXT PRIMARY KEY, owner TEXT NOT NULL,
            state TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at TEXT NOT NULL,
            actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, result TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, capture TEXT NOT NULL,
            name TEXT NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
            integrity TEXT NOT NULL DEFAULT 'unknown', expired INTEGER NOT NULL DEFAULT 0);
          PRAGMA user_version=1;
        """)
        self.db.execute("UPDATE operations SET status='uncertain' WHERE status='pending'")
        self.db.execute("UPDATE captures SET state='INTERRUPTED' WHERE state IN ('STARTING','RECORDING','STOPPING')")
        self.db.commit()
        (self.root / "captures").mkdir(mode=0o700, exist_ok=True)
        if (self.root / "captures").is_symlink():
            raise OSError("The captures directory cannot be a symbolic link.")

    def _audit(self, actor, action, target, result):
        self.db.execute("INSERT INTO audit(at,actor,action,target,result) VALUES (?,?,?,?,?)",
                        (utc(), actor, action, target, result))
        limits = self.config["limits"]
        cutoff = timestamp(time.time() - limits["audit_days"] * 86400)
        self.db.execute("DELETE FROM audit WHERE at < ?", (cutoff,))
        self.db.execute("DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT ?)",
                        (limits["audit_events"],))

    async def intention(self, actor, target, key, body, create=True):
        if self.failed:
            raise APIError(503, "STORAGE_UNAVAILABLE", "Metadata storage failed; restart after restoring storage. Active captures can still be stopped.")
        return await self.call(self._intention, actor, target, key, body, create)

    def _intention(self, actor, target, key, body, create=True):
        if not isinstance(key, str) or not 8 <= len(key) <= 200:
            raise APIError(400, "IDEMPOTENCY_KEY_REQUIRED", "Provide an Idempotency-Key of 8 to 200 characters.")
        digest = hashlib.sha256(dumps(body).encode()).hexdigest()
        existing = self.db.execute("SELECT * FROM operations WHERE actor=? AND target=? AND key=?",
                                   (actor, target, key)).fetchone()
        if existing:
            if existing["body_hash"] != digest:
                raise APIError(409, "IDEMPOTENCY_CONFLICT", "This key was already used with a different request.")
            return self.operation_row(existing), False
        if not create:
            raise APIError(404, "NOT_FOUND", "Capture or previous deletion operation not found.")
        now = time.time()
        with self.db:
            self.db.execute("DELETE FROM operations WHERE expires IS NOT NULL AND expires < ?", (now,))
            if self.db.execute("SELECT count(*) FROM operations").fetchone()[0] >= self.config["limits"]["max_operations"]:
                raise APIError(429, "OPERATION_QUOTA", "Operation quota reached; close unresolved operations or wait for expiry.")
            unresolved = self.db.execute("SELECT count(*) FROM operations WHERE status IN ('pending','uncertain')").fetchone()[0]
            if unresolved >= self.config["limits"]["max_unresolved_operations"]:
                raise APIError(429, "UNRESOLVED_OPERATION_QUOTA", "Unresolved operation quota reached; an administrator must review and close uncertain outcomes.")
            identity = uuid.uuid4().hex
            self.db.execute("INSERT INTO operations VALUES (?,?,?,?,?,'pending',NULL,?,NULL)",
                            (identity, actor, target, key, digest, now))
            self._audit(actor, "intent", target, identity)
        return {"id": identity, "actor": actor, "target": target, "status": "pending", "result": None}, True

    @staticmethod
    def operation_row(row):
        return {"id": row["id"], "actor": row["actor"], "target": row["target"], "status": row["status"],
                "result": loads(row["result"]) if row["result"] else None,
                "idempotency_expires_at": timestamp(row["expires"]) if row["expires"] else None}

    async def finish(self, identity, status, result):
        await self.call(self._finish, identity, status, result)

    def _finish(self, identity, status, result):
        with self.db:
            row = self.db.execute("SELECT * FROM operations WHERE id=?", (identity,)).fetchone()
            if not row:
                raise APIError(404, "NOT_FOUND", "Operation not found.")
            expiry = None if status in ("pending", "uncertain") else time.time() + 86400
            self.db.execute("UPDATE operations SET status=?,result=?,expires=? WHERE id=?",
                            (status, dumps(result), expiry, identity))
            self._audit(row["actor"], "result", row["target"], status)

    async def operation(self, identity):
        def query():
            row = self.db.execute("SELECT * FROM operations WHERE id=?", (identity,)).fetchone()
            if not row:
                raise APIError(404, "NOT_FOUND", "Operation not found.")
            return self.operation_row(row)
        return await self.call(query)

    async def close_unknown(self, identity, actor, note):
        def change():
            row = self.db.execute("SELECT * FROM operations WHERE id=?", (identity,)).fetchone()
            if not row or row["status"] != "uncertain":
                raise APIError(409, "OPERATION_STATE", "Only an uncertain operation can be closed.")
            self._finish(identity, "closed_unknown", {"result": "undetermined", "note": note})
            with self.db:
                self._audit(actor, "close_unknown", identity, note)
        await self.call(change)
        return await self.operation(identity)

    async def captures(self):
        def query():
            return [dict(loads(row["data"]), id=row["id"], owner=row["owner"], state=row["state"], revision=row["revision"])
                    for row in self.db.execute("SELECT * FROM captures ORDER BY rowid DESC")]
        return await self.call(query)

    async def save_capture(self, capture):
        def save():
            with self.db:
                self.db.execute("INSERT INTO captures VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                                "state=excluded.state,revision=excluded.revision,data=excluded.data",
                                (capture["id"], capture["owner"], capture["state"], capture["revision"], dumps(capture)))
        await self.call(save)

    async def audit(self):
        return await self.call(lambda: [dict(row) for row in self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100000")])

    async def close(self):
        def close():
            if self.db is not None:
                self.db.close()
            if self.lock is not None:
                self.lock.close()
        await asyncio.get_running_loop().run_in_executor(self.executor, close)
        self.executor.shutdown(wait=True)
