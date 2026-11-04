"""Authenticated HTTP and WebSocket API with an embedded, offline frontend."""
import asyncio
from contextlib import asynccontextmanager
import csv
import io
import logging
import os
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse, Response, StreamingResponse
from ose.transport.tcp import RemoteError
from ose_ws import __version__
from ose_ws.auth import Auth
from ose_ws.config import loopback
from ose_ws.schema.adapter import SchemaAdapter
from ose_ws.services.connection import ApplicationConnection
from ose_ws.services.logs import LogReader
from ose_ws.services.flows import FlowManager
from ose.execution.logs import LEVELS
from ose_ws.services.measurements import MeasurementStore
from ose_ws.services.operations import Operations
from ose_ws.services.parameters import ParameterManager
from ose_ws.services.recordings import RecordingManager
from ose_ws.services.status import StatusCollector
from ose_ws.storage.state import StateStore
from ose_ws.util import APIError, dumps, loads, opaque, page, public_numbers, require, utc


log = logging.getLogger("ose_ws")


async def body(request, limit=2 * 1024**2):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        require(size <= limit, "Request body exceeds its size limit.", 413, "BODY_TOO_LARGE")
        chunks.append(chunk)
    try:
        value = loads(b"".join(chunks) or b"{}", max_bytes=limit)
    except (ValueError, UnicodeDecodeError):
        raise APIError(400, "INVALID_JSON", "Invalid JSON, duplicate keys, non-finite numbers or excessive structure.") from None
    require(isinstance(value, dict), "The request body must be a JSON object.", 400)
    return value


def create_app(config):
    auth = Auth(config)
    connection = ApplicationConnection(config)
    logs = LogReader(connection)
    store = StateStore(config)
    measurements = MeasurementStore(config)
    collector = StatusCollector(config, connection, measurements)
    flows = FlowManager(config, connection, collector, auth)
    parameters = ParameterManager(config, connection, collector)
    recordings = RecordingManager(config, connection, store)
    operations = Operations(store)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await asyncio.to_thread(auth.load)
            await store.open()
            await recordings.open()
            await collector.start()
            await flows.start()
            await parameters.start()
            app.state.ready = True
            yield
        finally:
            app.state.ready = False
            await operations.close(config["limits"]["shutdown_s"])
            await flows.close()
            await recordings.close()
            await parameters.close()
            await collector.close()
            await logs.close()
            await store.close()

    app = FastAPI(title="OSE Web Console", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ready = False
    app.state.live_count = 0
    for name, service in (("auth", auth), ("connection", connection), ("store", store),
                          ("measurements", measurements), ("collector", collector), ("parameters", parameters),
                          ("recordings", recordings), ("operations", operations)):
        setattr(app.state, name, service)
    app.state.logs = logs
    app.state.flows = flows

    def session(request, *, mutation=False):
        return auth.get(request, mutation=mutation)

    def key(request):
        return request.headers.get("idempotency-key")

    def public(value):
        return JSONResponse(public_numbers(value))

    def error_response(request, error):
        return JSONResponse({"error": {"code": error.code, "message": error.message,
                            "request_id": getattr(request.state, "request_id", None), "details": error.details}}, status_code=error.status)

    @app.exception_handler(APIError)
    async def api_error(request, error):
        return error_response(request, error)

    @app.exception_handler(RemoteError)
    async def remote_error(request, error):
        denied = any(word in str(error).lower() for word in ("denied", "token", "identity", "client session"))
        return error_response(request, APIError(403 if denied else 422, "OSE_REFUSED", str(error)[:500]))

    @app.exception_handler(ValueError)
    @app.exception_handler(KeyError)
    @app.exception_handler(TypeError)
    async def invalid_value(request, error):
        return error_response(request, APIError(422, "INVALID_VALUE", str(error)[:500]))

    @app.exception_handler(OSError)
    @app.exception_handler(ConnectionError)
    @app.exception_handler(asyncio.TimeoutError)
    async def source_error(request, error):
        return error_response(request, APIError(503, "SOURCE_UNAVAILABLE", "The OSE service or local artifact is unavailable."))

    @app.exception_handler(ImportError)
    async def missing_dependency(request, error):
        return error_response(request, APIError(503, "DEPENDENCY_UNAVAILABLE", "Install the compatible SDL Python runtime and optional recording/filter dependencies before using this feature."))

    @app.middleware("http")
    async def headers(request, call_next):
        request.state.request_id = uuid.uuid4().hex
        started = time.monotonic()
        host = urlsplit("//" + request.headers.get("host", "")).hostname or ""
        if config["auth"]["mode"] == "local_token" and not loopback(host):
            response = error_response(request, APIError(403, "HOST_REJECTED", "Local mode requires a loopback Host header."))
        else:
            response = await call_next(request)
        response.headers.update({"X-Request-ID": request.state.request_id, "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY", "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        log.info(dumps({"request_id": request.state.request_id, "method": request.method,
                        "path": request.url.path, "status": response.status_code,
                        "duration_ms": round((time.monotonic() - started) * 1000, 2)}))
        return response

    @app.get("/health/live")
    async def live():
        return {"live": True}

    @app.get("/health/ready")
    async def ready():
        return JSONResponse({"ready": app.state.ready}, status_code=200 if app.state.ready else 503)

    @app.post("/api/v1/auth/login")
    async def login(request: Request):
        signed_in = auth.login(request, await body(request, 4096))
        response = public(signed_in.public())
        response.set_cookie(auth.cookie, signed_in.id, httponly=True, samesite="strict",
                            secure=config["auth"]["mode"] == "proxy", max_age=28800, path="/")
        return response

    @app.post("/api/v1/auth/logout")
    async def logout(request: Request):
        signed_in = session(request, mutation=True)
        await flows.release_session(signed_in.id)
        auth.sessions.pop(signed_in.id, None)
        response = Response(status_code=204)
        response.delete_cookie(auth.cookie)
        return response

    @app.get("/api/v1/session")
    async def current_session(request: Request):
        return session(request).public()

    @app.post("/api/v1/session/renew")
    async def renew_session(request: Request):
        return session(request, mutation=True).public()

    @app.get("/api/v1/openapi.json")
    async def openapi(request: Request):
        session(request).check("admin")
        return app.openapi()

    @app.get("/api/v1/application")
    async def application(request: Request):
        signed_in = session(request)
        value = collector.application() | {"server_version": __version__, "web_protocol": 1,
            "recording_error": recordings.error, "parameter_error": parameters.error,
            "storage_available": not store.failed}
        if not signed_in.allows("admin") and not (signed_in.permissions.get("parameters.read") or signed_in.permissions.get("parameters.write")):
            value.pop("parameter_error")
        return public(value)

    @app.get("/api/v1/components")
    async def components(request: Request):
        session(request).check("components.read")
        return public(collector.diagnostics())

    @app.get("/api/v1/flow/schema")
    async def flow_schema(request: Request, subscription: str, epoch: str):
        session(request).check("components.read")
        require(collector.error is None, "The OSE application is unavailable.", 503, "SOURCE_UNAVAILABLE")
        require(epoch == collector.snapshot.get("epoch"), "The application changed; refresh its status.", 409, "APPLICATION_CHANGED")
        subscriptions = collector.snapshot.get("observation", {}).get("subscriptions", {})
        require(subscription in subscriptions, "Subscription not found.", 404, "NOT_FOUND")
        client = await connection.connect()
        try:
            desc, catalogue = await client.request("subscription_schema", subscription=subscription, timeout=3)
        finally:
            await client.close()
        require(desc.get("epoch") == epoch == collector.snapshot.get("epoch"),
                "The application changed; refresh its status.", 409, "APPLICATION_CHANGED")
        adapter = await asyncio.to_thread(SchemaAdapter, catalogue.decode(), desc["root"])
        return public(await asyncio.to_thread(adapter.description))

    @app.post("/api/v1/flow-watches")
    async def flow_watch(request: Request):
        signed_in = session(request, mutation=True)
        return await flows.watch(signed_in, await body(request, 4096))

    @app.get("/api/v1/flow-watches/{identity}")
    async def flow_snapshot(request: Request, identity: str):
        return public(flows.snapshot(session(request), identity))

    @app.delete("/api/v1/flow-watches/{identity}")
    async def flow_stop(request: Request, identity: str):
        await flows.remove(session(request, mutation=True), identity)
        return Response(status_code=204)

    @app.get('/api/v1/logs')
    async def log_records(request: Request, components: str = '*', levels: str = '', search: str = '',
                          logger: str = '*', after: str = None, epoch: str = None, limit: int = 200):
        signed_in = session(request)
        scopes = ['*'] if signed_in.allows('admin') else signed_in.permissions.get('logs.read', [])
        require(bool(scopes), 'Permission denied.', 403, 'FORBIDDEN')
        selected_levels = levels.split(',') if levels else None
        require(selected_levels is None or all(level in LEVELS.values() for level in selected_levels), 'Invalid log levels.')
        require(1 <= limit <= 200 and len(search) <= 500 and len(logger) <= 200 and len(components) <= 2000,
                'Invalid log filters or page size.')
        require(after is None or after.isascii() and after.isdecimal() and len(after) <= 20, 'Invalid log cursor.')
        result = await logs.query(dict(components=components.split(','), scopes=scopes, levels=selected_levels,
                                       search=search, logger=logger, after=after, epoch=epoch, limit=limit))
        return public(result)

    @app.get("/api/v1/components/{identity}")
    async def component(request: Request, identity: str):
        session(request).check("components.read")
        row = next((r for r in collector.report.get("components", []) if opaque("component", r["name"]) == identity or r["name"] == identity), None)
        require(row is not None, "Component not found.", 404)
        return public(row)

    @app.get("/api/v1/measurements")
    async def measurement_catalogue(request: Request, cursor: str = None, limit: int = 200, search: str = ""):
        signed_in = session(request)
        items = [item for item in measurements.catalogue.values()
                 if signed_in.allows("measurements.read", item["name"]) and search.lower() in item["name"].lower()]
        # Cursor identity is based on inventory, not constantly changing current values.
        identities = page(sorted(item["id"] for item in items), cursor, limit)
        return public({"items": [measurements.catalogue[identity] for identity in identities["items"]], "next": identities["next"],
                       "observed_at": collector.last_success, "error": collector.error})

    def measurement_ids(signed_in, ids):
        require(isinstance(ids, list) and bool(ids), "Select measurement identifiers.")
        for identity in ids:
            require(identity in measurements.catalogue, "Measurement not found.", 404)
            signed_in.check("measurements.read", measurements.catalogue[identity]["name"])
        return ids

    @app.post("/api/v1/measurement-watches")
    async def watch(request: Request):
        signed_in, data = session(request, mutation=True), await body(request)
        ids = measurement_ids(signed_in, data.get("measurement_ids"))
        return measurements.watch(signed_in.actor, ids, data.get("id"))

    @app.delete("/api/v1/measurement-watches/{identity}")
    async def unwatch(request: Request, identity: str):
        measurements.unwatch(session(request, mutation=True).actor, identity)
        return Response(status_code=204)

    @app.get("/api/v1/series")
    async def series(request: Request, ids: str, start: float = None, end: float = None, max_points: int = 5000, cursor: str = None):
        selected = measurement_ids(session(request), ids.split(","))
        require(start is None or end is None or start <= end, "Invalid time window.")
        require(8 <= max_points <= 5000 and len(selected) <= 128, "Invalid history point or series budget.")
        matching, next_cursor = measurements.snapshot(selected, cursor)
        result = await asyncio.to_thread(measurements.render, matching, start, end, max_points, next_cursor)
        return await asyncio.to_thread(public, result)

    @app.get("/api/v1/series/export")
    async def series_export(request: Request, ids: str):
        selected = set(measurement_ids(session(request), ids.split(",")))
        measurements.evict()
        # A bounded snapshot protects this download from concurrent buffer eviction.
        snapshot = [(segment["id"], segment["name"], list(segment["points"])) for segment in measurements.segments.values()
                    if segment["measurement_id"] in selected]
        def stream():
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["observed_at", "sequence", "segment", "measurement", "value", "quality"])
            yield output.getvalue()
            for segment_id, name, points in snapshot:
                for point in points:
                    output.seek(0)
                    output.truncate(0)
                    from ose_ws.util import timestamp
                    writer.writerow([timestamp(point[1]), point[0], segment_id, name, point[3], point[4]])
                    yield output.getvalue()
        return StreamingResponse(stream(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="observations.csv"'})

    @app.get("/api/v1/recording-points")
    async def recording_points(request: Request, cursor: str = None, limit: int = 200, search: str = "", kind: str = ""):
        signed_in = session(request)
        await recordings.refresh()
        visible = [dict(point, id=opaque("point", topic)) for topic, point in sorted(recordings.points.items())
                   if signed_in.allows("recordings.create", topic) and search.lower() in topic.lower() and (not kind or kind == point.get("kind"))]
        # Endpoint details are used by the server, never trusted from a browser.
        for point in visible:
            point.pop("endpoint", None)
        return public(page(visible, cursor, limit))

    @app.post("/api/v1/recording-points/refresh")
    async def recording_refresh(request: Request):
        signed_in = session(request, mutation=True)
        require(signed_in.allows("admin") or bool(signed_in.permissions.get("recordings.create")), "Permission denied.", 403)
        await recordings.refresh(force=True)
        return {"refreshed_at": utc()}

    @app.get("/api/v1/recording-points/{identity}/schema")
    async def recording_schema(request: Request, identity: str):
        signed_in = session(request)
        await recordings.refresh()
        require(identity in recordings.point_ids, "Recording point not found.", 404)
        signed_in.check("recordings.create", recordings.point_ids[identity])
        desc = await recordings.describe(identity)
        adapter = await asyncio.to_thread(SchemaAdapter, desc["catalogue"], desc["root"])
        return await asyncio.to_thread(adapter.description)

    @app.post("/api/v1/recording-filters/validate")
    async def validate_filter(request: Request):
        signed_in, data = session(request, mutation=True), await body(request)
        await recordings.refresh()
        topic = recordings.point_ids.get(data.get("point_id"))
        require(topic is not None, "Recording point not found.", 404)
        signed_in.check("recordings.create", topic)
        require(isinstance(data.get("expression"), str) and len(data["expression"]) <= 16384, "Invalid filter expression.")
        desc = await recordings.describe(data["point_id"])
        def validate():
            from ose.recording.filters import prepare_filter
            predicate = prepare_filter(desc, data["expression"])
            predicate.close()
        await asyncio.to_thread(validate)
        return {"valid": True, "location": "source"}

    @app.get("/api/v1/recordings")
    async def list_recordings(request: Request, cursor: str = None, limit: int = 200):
        signed_in = session(request)
        visible = [recordings.public(capture, signed_in) for capture in recordings.objects.values()
                   if recordings.allowed(signed_in, capture, "recordings.read")]
        identities = page(sorted(c["id"] for c in visible), cursor, limit)
        return public({"items": [c for c in visible if c["id"] in identities["items"]], "next": identities["next"]})

    @app.post("/api/v1/recordings", status_code=201)
    async def create_recording(request: Request):
        signed_in, data = session(request, mutation=True), await body(request)
        operation = await operations.run(signed_in, "recordings.create", key(request), data, lambda: recordings.create(signed_in, data))
        return JSONResponse(public_numbers(operation), status_code=201)

    @app.get("/api/v1/recordings/{identity}")
    async def get_recording(request: Request, identity: str):
        signed_in = session(request)
        return public(recordings.public(recordings.get(signed_in, identity), signed_in))

    @app.delete("/api/v1/recordings/{identity}")
    async def delete_recording(request: Request, identity: str):
        signed_in, data = session(request, mutation=True), await body(request)
        require(set(data) == {"delete_files"} and type(data["delete_files"]) is bool,
                "Choose whether to delete capture files with a boolean delete_files field.")
        exists = identity in recordings.objects
        if exists:
            recordings.get(signed_in, identity, "recordings.delete")
        else:
            signed_in.check("recordings.delete", identity)
        return public(await operations.run(signed_in, f"recordings/{identity}/delete", key(request), data,
                                           lambda: recordings.delete(signed_in, identity, data["delete_files"]),
                                           replay_only=not exists))

    @app.patch("/api/v1/recordings/{identity}")
    async def patch_recording(request: Request, identity: str):
        signed_in, data = session(request, mutation=True), await body(request)
        recordings.get(signed_in, identity, "recordings.create")
        return public(await operations.run(signed_in, f"recordings/{identity}/patch", key(request), data,
                                           lambda: recordings.patch(signed_in, identity, data)))

    @app.post("/api/v1/recordings/{identity}/start", status_code=202)
    async def start_recording(request: Request, identity: str):
        signed_in = session(request, mutation=True)
        recordings.get(signed_in, identity, "recordings.create")
        operation = await operations.run(signed_in, f"recordings/{identity}/start", key(request), {},
                                         lambda: recordings.start(signed_in, identity), background=True)
        return JSONResponse(operation, status_code=202, headers={"Location": f"/api/v1/operations/{operation['id']}"})

    @app.post("/api/v1/recordings/{identity}/stop", status_code=202)
    async def stop_recording(request: Request, identity: str):
        signed_in = session(request, mutation=True)
        recordings.get(signed_in, identity, "recordings.stop")
        operation = await operations.run(signed_in, f"recordings/{identity}/stop", key(request), {},
                                         lambda: recordings.stop(signed_in, identity), background=True, emergency_stop=True)
        return JSONResponse(operation, status_code=202, headers={"Location": f"/api/v1/operations/{operation['id']}"})

    @app.get("/api/v1/recordings/{identity}/files")
    async def capture_files(request: Request, identity: str):
        return public({"items": await recordings.files(session(request), identity)})

    @app.get("/api/v1/files/{identity}/download")
    async def download(request: Request, identity: str):
        fd, name = await recordings.open_file(session(request), identity)
        def stream():
            with os.fdopen(fd, "rb") as source:
                while chunk := source.read(65536):
                    yield chunk
        return StreamingResponse(stream(), media_type="application/octet-stream",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/v1/files/{identity}/validate", status_code=202)
    async def validate_file(request: Request, identity: str):
        signed_in = session(request, mutation=True)
        fd, _ = await recordings.open_file(signed_in, identity)
        os.close(fd)
        operation = await operations.run(signed_in, f"files/{identity}/validate", key(request), {},
                                         lambda: recordings.validate_file(signed_in, identity), background=True)
        return JSONResponse(operation, status_code=202, headers={"Location": f"/api/v1/operations/{operation['id']}"})

    @app.get("/api/v1/parameters")
    async def parameter_list(request: Request, cursor: str = None, limit: int = 200):
        signed_in = session(request)
        if time.monotonic() - parameters.refreshed > 5:
            await parameters.refresh()
        items = [{"id": identity, "path": path, "permissions": {"read": signed_in.allows("parameters.read", path),
                  "write": signed_in.allows("parameters.write", path)}, "permissions_source": "web_policy"}
                 for identity, path in parameters.inventory.items()
                 if signed_in.allows("parameters.read", path) or signed_in.allows("parameters.write", path)]
        return page(items, cursor, limit)

    async def parameter_path(request, identity, permission, *, mutation=False):
        signed_in = session(request, mutation=mutation)
        path = await parameters.path(identity)
        if permission == "parameters.describe":
            require(signed_in.allows("parameters.read", path) or signed_in.allows("parameters.write", path), "Permission denied.", 403)
        else:
            signed_in.check(permission, path)
        return signed_in, path

    @app.get("/api/v1/parameters/{identity}/schema")
    async def parameter_schema(request: Request, identity: str):
        _, path = await parameter_path(request, identity, "parameters.describe")
        schema = await parameters.schema(path)
        return await asyncio.to_thread(schema.description)

    @app.get("/api/v1/parameters/{identity}")
    async def parameter_value(request: Request, identity: str):
        signed_in, path = await parameter_path(request, identity, "parameters.read")
        return await parameters.get(path) | {"block_id": identity, "path": path,
            "permissions": {"read": True, "write": signed_in.allows("parameters.write", path)}, "permissions_source": "web_policy"}

    @app.post("/api/v1/parameters/{identity}/validate")
    async def parameter_validate(request: Request, identity: str):
        _, path = await parameter_path(request, identity, "parameters.describe", mutation=True)
        return await parameters.validate(path, await body(request))

    @app.put("/api/v1/parameters/{identity}")
    async def parameter_set(request: Request, identity: str):
        signed_in, path = await parameter_path(request, identity, "parameters.write", mutation=True)
        data = await body(request)
        # Access is checked before looking up an existing idempotency result.
        return public(await operations.run(signed_in, f"parameters/{identity}", key(request), data, lambda: parameters.set(path, data)))

    @app.post("/api/v1/parameter-imports/preview")
    async def import_preview(request: Request):
        signed_in = session(request, mutation=True)
        document = await body(request, 16 * 1024**2)
        return await parameters.preview_import(signed_in, document)

    @app.post("/api/v1/parameter-imports/{identity}/apply", status_code=202)
    async def apply_import(request: Request, identity: str):
        signed_in = session(request, mutation=True)
        # Resolve idempotency before the volatile import plan: retries after a server
        # restart must return the durable operation rather than dispatch again.
        async def effect():
            plan = parameters.import_plan(signed_in, identity)
            for block in plan["blocks"]:
                signed_in.check("parameters.write", block["path"])
            return await parameters.apply_import(signed_in, plan)
        operation = await operations.run(signed_in, f"parameter-imports/{identity}/apply", key(request), {}, effect, background=True)
        return JSONResponse(operation, status_code=202, headers={"Location": f"/api/v1/operations/{operation['id']}"})

    @app.post("/api/v1/parameter-exports")
    async def export_parameters(request: Request):
        signed_in, data = session(request, mutation=True), await body(request)
        require(isinstance(data.get("block_ids"), list) and len(data["block_ids"]) <= 4096, "Select blocks to export.")
        values = {}
        for identity in data["block_ids"]:
            path = await parameters.path(identity)
            signed_in.check("parameters.read", path)
            value = await parameters.get(path)
            schema = await parameters.schema(path)
            values[path] = schema.convert(value["value"], to_web=False)
        return Response(dumps(values), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="parameters.json"'})

    @app.get("/api/v1/operations")
    async def operation_list(request: Request, cursor: str = None, limit: int = 200):
        signed_in = session(request)
        rows = await store.call(lambda: [store.operation_row(row) for row in store.db.execute("SELECT * FROM operations WHERE status='uncertain' ORDER BY created DESC")])
        return page([row for row in rows if row["actor"] == signed_in.actor or signed_in.allows("admin")], cursor, limit)

    @app.get("/api/v1/operations/{identity}")
    async def operation_value(request: Request, identity: str):
        signed_in = session(request)
        operation = await operations.get(identity)
        require(operation["actor"] == signed_in.actor or signed_in.allows("admin"), "Operation not found.", 404)
        if operation["target"].startswith("parameters/"):
            block_id = operation["target"].split("/")[1]
            path = await parameters.path(block_id)
            signed_in.check("parameters.write", path)
        return public(operation)

    @app.post("/api/v1/operations/{identity}/close-unknown")
    async def close_unknown(request: Request, identity: str):
        signed_in, data = session(request, mutation=True), await body(request)
        signed_in.check("admin")
        require(identity not in operations.tasks, "The operation is still running.", 409)
        require(isinstance(data.get("note"), str) and 1 <= len(data["note"]) <= 1000, "A closure note is required.")
        return await store.close_unknown(identity, signed_in.actor, data["note"])

    @app.get("/api/v1/audit")
    async def audit(request: Request, cursor: str = None, limit: int = 200):
        session(request).check("audit.read")
        return page(await store.audit(), cursor, limit)

    @app.websocket("/api/v1/live")
    async def websocket(socket: WebSocket):
        try:
            signed_in = auth.get(socket)
            auth.same_origin(socket)
            require(bool(socket.headers.get("origin")), "WebSocket Origin is required.", 403)
            require(app.state.live_count < config["auth"]["max_sessions"], "Live connection quota reached.", 429)
        except APIError:
            await socket.close(code=4403)
            return
        app.state.live_count += 1
        try:
            await socket.accept()
        except Exception:
            app.state.live_count -= 1
            return
        subscriptions = {}
        stream_id, sequence = uuid.uuid4().hex, 0
        last_pong, last_ping = time.monotonic(), 0

        async def emit(kind, subscription_id, data):
            nonlocal sequence
            auth.get(socket)
            sequence += 1
            message = dumps(public_numbers({"protocol": 1, "stream_id": stream_id, "sequence": str(sequence),
                "subscription_id": subscription_id, "type": kind, "observed_at": utc(), "data": data}))
            require(len(message.encode()) <= 256 * 1024, "WebSocket message is too large.", 413)
            await asyncio.wait_for(socket.send_text(message), 2)

        def authorize(stream, selection):
            if stream == "flow":
                require(isinstance(selection, list) and len(selection) == 1 and isinstance(selection[0], str), "Select one Live watch.")
                flows.get(signed_in, selection[0])
            elif stream == "components":
                signed_in.check("components.read")
            elif stream == "measurements":
                measurement_ids(signed_in, selection)
            elif stream == "parameters":
                require(isinstance(selection, list), "Select parameter block identifiers.")
                for identity in selection:
                    require(identity in parameters.inventory, "Parameter block not found.", 404)
                    signed_in.check("parameters.read", parameters.inventory[identity])
            elif stream == "recordings":
                require(signed_in.allows("admin") or bool(signed_in.permissions.get("recordings.read")), "Permission denied.", 403)
            elif stream not in ("application", "operations"):
                raise APIError(400, "INVALID_STREAM", "Unknown stream.")

        try:
            while True:
                now = time.monotonic()
                auth.get(socket)
                if now - last_ping >= 15:
                    await emit("ping", None, {})
                    last_ping = now
                if now - last_pong > 45:
                    break
                try:
                    raw = await asyncio.wait_for(socket.receive_text(), .25)
                    try:
                        data = loads(raw, max_bytes=256 * 1024)
                    except ValueError:
                        raise APIError(400, "INVALID_JSON", "Invalid WebSocket JSON.") from None
                    require(isinstance(data, dict), "A WebSocket command must be an object.", 400)
                    if data.get("action") == "pong":
                        last_pong = now
                    elif data.get("action") == "unsubscribe":
                        subscriptions.pop(data.get("subscription_id"), None)
                    elif data.get("action") == "subscribe":
                        require(len(subscriptions) < 32, "Subscription quota reached.", 429)
                        identity = data.get("subscription_id")
                        require(isinstance(identity, str) and 1 <= len(identity) <= 100, "A subscription_id is required.", 400)
                        stream, selection = data.get("stream"), data.get("selection", [])
                        authorize(stream, selection)
                        cadence = data.get("interval_ms", 1000)
                        require(type(cadence) is int and 250 <= cadence <= 60000, "Invalid stream cadence.")
                        subscriptions[identity] = {"stream": stream, "selection": selection, "interval": cadence / 1000,
                                                   "sent": 0, "cursor": measurements.sequence, "changes": {}}
                        await emit("snapshot_required", identity, {"stream": stream})
                    else:
                        raise APIError(400, "INVALID_ACTION", "Unknown WebSocket action.")
                except asyncio.TimeoutError:
                    pass
                for identity, sub in list(subscriptions.items()):
                    if now - sub["sent"] < sub["interval"]:
                        continue
                    if sub["stream"] == "flow":
                        sub["sent"] = now
                        watch_id = sub["selection"][0]
                        try:
                            snapshot = flows.snapshot(signed_in, watch_id)
                            change = (snapshot["version"], snapshot["status"])
                            if sub["changes"].get(watch_id) != change:
                                sub["changes"][watch_id] = change
                                await emit("flow.sample", identity, snapshot)
                        except APIError as error:
                            await emit("flow.sample", identity, {"watch_id": watch_id, "status": "unavailable", "error": error.message, "sample": None})
                            subscriptions.pop(identity, None)
                        continue
                    authorize(sub["stream"], sub["selection"])
                    sub["sent"] = now
                    stream = sub["stream"]
                    if stream == "measurements":
                        segments, total = [], 0
                        evicted = bool(measurements.order and measurements.order[0][1][0] > sub["cursor"] + 1)
                        for segment in measurements.segments.values():
                            if segment["measurement_id"] not in sub["selection"]:
                                continue
                            points = [p for p in segment["points"] if p[0] > sub["cursor"]]
                            total += len(points)
                            for offset in range(0, min(len(points), 4096), 128):
                                if total > 4096:
                                    break
                                segments.append({"segment_id": segment["id"], "measurement_id": segment["measurement_id"],
                                    "points": [{"sequence": str(p[0]), "time": p[1] * 1000, "value": p[3], "quality": p[4]} for p in points[offset:offset + 128]]})
                        if total > 4096 or evicted:
                            await emit("resync_required", identity, {"reason": "Point buffer exceeded; reload HTTP history."})
                        else:
                            for segment in segments:
                                await emit("measurements.points", identity, segment)
                        sub["cursor"] = measurements.sequence
                    elif stream == "parameters":
                        for block in sub["selection"]:
                            changed = parameters.changes.get(block, 0)
                            if sub["changes"].get(block) != changed:
                                sub["changes"][block] = changed
                                await emit("parameters.changed", identity, {"block_id": block})
                    else:
                        await emit(stream + ".changed", identity, {"refresh": True})
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            pass
        except APIError as error:
            try:
                await socket.send_text(dumps({"type": "error", "error": {"code": error.code, "message": error.message}}))
                await socket.close(code=4401 if error.status == 401 else 4403)
            except (RuntimeError, WebSocketDisconnect):
                pass
        finally:
            app.state.live_count -= 1
            for sub in subscriptions.values():
                if sub["stream"] == "flow":
                    try:
                        await flows.remove(signed_in, sub["selection"][0])
                    except APIError:
                        pass
            try:
                await socket.close(code=1000)
            except (RuntimeError, WebSocketDisconnect):
                pass

    static = Path(__file__).parent / "static"

    @app.get("/")
    async def frontend():
        if not (static / "index.html").is_file():
            return JSONResponse({"error": {"code": "FRONTEND_NOT_BUILT", "message": "Build the frontend before packaging ose-ws."}}, status_code=503)
        return FileResponse(static / "index.html")

    @app.get("/assets/{name}")
    async def asset(name: str):
        require("/" not in name and "\\" not in name and name not in (".", ".."), "Asset not found.", 404)
        path = static / "assets" / name
        require(path.is_file() and not path.is_symlink(), "Asset not found.", 404)
        return FileResponse(path)

    return app
