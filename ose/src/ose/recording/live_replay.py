"""Publish preflighted recordings to a live local broker or coordinator."""
from ose.execution.plan import read_plan
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import time
import uuid

from ose.codec.sdl2 import SDL2
from ose.execution.publications import topic_contracts
from ose.execution.simulation.clock import seconds_ns
from ose.recording.control import ReplayControl
from ose.recording.pacing import playback_speed
from ose.recording.replay import RepeatedEvents, loop_count, recording_events, topic_mapping
from ose.transport.endpoint import resolve
from ose.transport.secure import connect, finish_cleanup
from ose.transport.udp import UDP


@asynccontextmanager
async def publisher(plan, topics, *, host, port, distributed=False, context=None):
    client, udp, token = None, None, None
    try:
        client = await connect(host, port, plan["max_frame"], context,
                               "coordinator" if distributed else None)
        client.request_timeout = 15 + max((sub["policy"]["wait_timeout_ms"] / 1000
            for sub in plan["subscriptions"] if sub["policy"]["overflow"] == "wait"), default=0)
        if distributed:
            from ose.execution.distributed import fingerprint
            result, _ = await client.request("external_open", topics=sorted(topics), plan_id=fingerprint(plan))
            token = result["token"]
            # READY precedes the first activation heartbeat. Wait until existing
            # workers actually run their schedulers, so startup does not fill
            # and overflow input queues before any callback can consume them.
            deadline = time.monotonic() + 20
            while True:
                status, _ = await client.request("status")
                if all(status.get('component_states', {}).get(name, {}).get('active') is False or
                       node.get('metrics', {}).get('running') for name, node in status['components'].items()):
                    break
                if time.monotonic() >= deadline:
                    raise ValueError("workers did not start processing within 20 seconds")
                await asyncio.sleep(.05)
        else:
            async def ignore(*args):
                pass
            udp = await UDP.bind(ignore)
            result, _ = await client.request("client_hello", client_id="replay-" + uuid.uuid4().hex,
                                              udp_port=udp.address[1])
            client.tcp.limit = min(client.tcp.limit, result["max_frame"])
            client.request_timeout = 15 + result["max_wait_timeout_ms"] / 1000
            contracts = topic_contracts(plan)
            for topic in topics:
                desc = contracts[topic]
                await client.request("declare_publisher", desc["catalogue"].encode(), topic=topic, root=desc["root"])

        async def publish(topic, body, *, app_id=None):
            extra = {"app_id": app_id} if app_id is not None else {}
            result, _ = (await client.request("external_publish", body, token=token, topic=topic, **extra)
                         if distributed else await client.request("publish", body, topic=topic, **extra))
            return result["delivery"]
        yield publish
    finally:
        async def close():
            if client is not None:
                try:
                    if token is not None:
                        await client.request("external_close", token=token, timeout=.5)
                except (OSError, EOFError, ValueError, asyncio.TimeoutError):
                    pass
                finally:
                    await client.close()
            if udp is not None:
                await udp.close()
        await finish_cleanup(close())


async def play(events, publish, *, speed=1, duration=None, loops=1, interactive=False, commands=None, output=None, capture=None):
    output = sys.stderr if output is None else output
    session = ReplayControl("realtime", interactive=interactive, commands=commands, speed=speed, output=output)
    horizon = seconds_ns(duration, positive=True) if duration is not None else None
    repeated = RepeatedEvents(events, loops)
    status = dict(published=0, refused=0, loops=loops, current_loop=1, completed_loops=0,
                  last_lag_seconds=0.0, max_lag_seconds=0.0)
    deliveries, started, last_report = Counter(), time.monotonic(), time.monotonic()
    if capture is not None:
        capture.follow(session)
    session.snapshot = lambda: {**status, "delivery_results": dict(deliveries)}
    session.start()
    reason = "complete"
    try:
        await asyncio.sleep(0)  # Apply leading controls before the first publication.
        for index, event in enumerate(repeated):
            status["current_loop"] = index // len(events) + 1
            if horizon is not None and event.at >= horizon:
                boundary = (await session.live_wait(horizon, terminal=True)
                            if session.boundary() is not None else 0)
                reason = "stopped" if boundary is None else "duration"
                break
            lag = await session.live_wait(event.at)
            if lag is None:
                reason = "stopped"
                break
            status["last_lag_seconds"] = lag
            status["max_lag_seconds"] = max(lag, status["max_lag_seconds"])
            app_id = getattr(event, "app_id", None)
            extra = {"app_id": app_id} if app_id is not None else {}
            async with session.busy:
                deliveries.update((await publish(event.topic, event.read(), **extra)).values())
            status["published"] += 1
            session.time_entered = True
            if (index + 1) % len(events) == 0:
                status["completed_loops"] += 1
            status["refused"] = sum(count for result, count in deliveries.items()
                                    if result not in ("accepted", "duplicate", "stale"))
            if session.quota is not None:
                session.quota -= 1
                session.units += 1
                if session.quota == 0:
                    session.complete()
            if session.instant_quota is not None:
                try:
                    next_at = repeated[index + 1].at
                except IndexError:
                    next_at = None
                if next_at != event.at and event.at > session.operation[1]:
                    session.instant_quota -= 1
                    session.units += 1
                    if session.instant_quota == 0:
                        session.complete()
            if lag > .05 and time.monotonic() - last_report >= 1:
                print(f"replay: lag {lag:.3f}s; published {status['published']}; refused {status['refused']}",
                      file=output, flush=True)
                last_report = time.monotonic()
            if (index + 1) % len(events) == 0:
                # Even a one-message, unlimited-speed replay must yield to
                # scripts, interactive commands and cancellation at each loop.
                await asyncio.sleep(0)
        if session.boundary() is not None and reason == "complete":
            # A finite selection ends one nanosecond after its last message.
            end = events[-1].at + (loops - 1) * repeated.period_ns + 1
            if await session.live_wait(min(end, horizon) if horizon is not None else end, terminal=True) is None:
                reason = "stopped"
        if session.error is not None:
            raise session.error
        await session.finish(reason)
        return {"mode": "realtime", "stop_reason": reason, **status, "speed": session.clock.speed,
                "position_ns": session.position(), "paused": session.paused, "last_command": session.last_command,
                "breakpoints_ns": sorted(session.breakpoints), "last_breakpoint_ns": session.last_breakpoint,
                "delivery_results": dict(deliveries), "elapsed_seconds": time.monotonic() - started}
    finally:
        await session.close()


async def run(path, plan_path, duration=None, *, mappings=None, speed=1, start_ns=None, end_ns=None,
              endpoint=None, host=None, port=None, distributed=False, tls_ca=None, tls_cert=None,
              tls_key=None, insecure=False, interactive=False, commands=None, loops=1, schema_policy='exact', record=None, record_file=None,
              record_from=None, record_until=None):
    playback_speed(speed)
    loop_count(loops)
    if duration is not None:
        seconds_ns(duration, positive=True)
    plan_path, path = Path(plan_path).resolve(), Path(path).resolve()
    plan = read_plan(plan_path)
    from ose.execution.identity import identify
    identify("replay", plan)
    if path in (plan_path, (Path(plan["build_dir"]) / "last-status.json").resolve()):
        raise ValueError("recording cannot use the compiled plan or last-status.json path")
    from ose.recording.batch import destination, LiveCapture, CaptureWindow
    CaptureWindow(record, record_from, record_until)
    destination(record, record_file, protected=[path, plan_path, Path(plan['build_dir']) / 'last-status.json',
                                                Path(plan['build_dir']) / 'endpoint.json'])
    topics = topic_contracts(plan)
    mapping = topic_mapping(mappings, topics)
    codec = SDL2(plan["sdl_root"])
    if endpoint is not None and (host is not None or port is not None):
        raise ValueError("choose --endpoint or --host/--port")
    default_endpoint = Path(plan["build_dir"]) / "endpoint.json"
    endpoint = endpoint or (str(default_endpoint) if host is None and port is None and default_endpoint.is_file() else None)
    address = resolve(dict(endpoint=endpoint, host=host, port=port,
        distributed=distributed or (not endpoint and plan.get("execution") == "distributed"),
        insecure=insecure, tls_ca=tls_ca, tls_cert=tls_cert, tls_key=tls_key))
    host, port, distributed, context = address.host, address.port, address.distributed, address.context
    with recording_events(path, topics, mapping, codec, start_ns=start_ns, end_ns=end_ns,
                          schema_policy=schema_policy) as (events, selection):
        async with publisher(plan, selection["mapping"].values(), host=host, port=port,
                             distributed=distributed, context=context) as publish:
            async with LiveCapture(record, record_file, host=host, port=port, context=context,
                                   distributed=distributed, sdl_root=plan['sdl_root'],
                                   record_from=record_from, record_until=record_until) as capture:
                result = await capture.watch(play(events, publish, speed=speed, duration=duration, loops=loops,
                                                  interactive=interactive, commands=commands, capture=capture))
        selection.update(loops=loops, loop_period_ns=events[-1].at + 1)
    result["replay"] = selection
    return result
