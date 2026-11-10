"""Shared CH/2 lifecycle and input callbacks for real and simulated execution."""
import asyncio
import json
import os
from pathlib import Path
from ose.execution import ipc
from ose.parameters.bindings import updates, write_reply
from ose.execution.measurements import Measurements
from ose.execution.logs import ComponentLogs


async def start_component(node, parameters=None, recording=None, logs=None, *, working_directory=None, inputs=None):
    if type(node.get("execution_model")) is not int or node["execution_model"] != 2:
        raise ValueError("component execution requires model 2; rebuild the container")
    if inputs is None:
        raise ValueError("component execution requires CH/2 input services")
    env = dict(os.environ, **node.get("env", {}))
    from ose.execution.identity import component_environment
    env.update(component_environment(node))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    # Terminal Ctrl+C belongs to the supervisor. Components stop through IPC so
    # callbacks can finish and on_stop runs instead of a concurrent SIGINT.
    process = await asyncio.create_subprocess_exec(*node["command"], cwd=working_directory if working_directory is not None else node["cwd"], env=env,
        start_new_session=True,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    if recording is None:
        from ose.recording.component import ComponentRecording
        recording = ComponentRecording(None)
    process.recording = recording
    process.measurements = Measurements()
    process.logs = logs if logs is not None else ComponentLogs()
    try:
        async def initialize():
            await ipc.send(process.stdin, "I", json.dumps(node).encode())
            if node.get("parameter_bindings"):
                if parameters is None:
                    raise ValueError("shared parameter service is required")
                await updates(process, node, parameters.initial)
            op, body = await ipc.receive(process.stdout)
            while op in ("S", "N", "M", "L", "Q"):
                if op == "Q":
                    if inputs is None:
                        raise ValueError("input services require CH/2")
                    await ipc.send(process.stdin, "A", inputs.handle(body, initializing=True))
                elif op == 'L':
                    process.logs.emit(body)
                elif op == 'M':
                    process.measurements.declare(body)
                elif op == "N":
                    await recording.declare(body)
                else:
                    await write_reply(process, body, parameters)
                op, body = await ipc.receive(process.stdout)
            from ose.execution.input_ipc import READY
            if op != "R" or body != READY:
                raise ValueError("execution model 2 requires a CH/2 component SDK")
        initialization = asyncio.create_task(initialize())
        try:
            # Python 3.9/3.10 wait_for can lose cancellation when READY arrives
            # in the same loop turn. Shutdown must still stop the component.
            done, _ = await asyncio.wait({initialization}, timeout=15)
            if not done:
                raise asyncio.TimeoutError()
            initialization.result()
        finally:
            if not initialization.done():
                initialization.cancel()
            # Release the stdout reader before stop_component drains the pipe.
            await asyncio.gather(initialization, return_exceptions=True)
        return process
    except BaseException:
        await stop_component(process)
        raise


async def input_events(process, node, reference, index, services, parameters=None):
    """Drive one CH/2 callback; leave consumption commit to the owning engine."""
    from ose.execution.input_ipc import activation_data, unpack_publication
    if parameters is not None:
        await updates(process, node, parameters.drain())
    recording = getattr(process, "recording", None)
    if recording is not None and recording.points:
        activation = recording.activation()
        if activation != getattr(process, "recording_activation", None):
            await ipc.send(process.stdin, "E", activation)
            process.recording_activation = activation
    services.begin()
    await ipc.send(process.stdin, "T", activation_data(reference, index))
    recording_bytes = 0
    while True:
        op, body = await ipc.receive(process.stdout)
        if op == "F" and not body:
            return
        if op == "Q":
            await ipc.send(process.stdin, "A", services.handle(body))
        elif op == "S":
            await write_reply(process, body, parameters)
        elif op == "L":
            process.logs.emit(body)
        elif op == "V":
            process.measurements.update(body)
        elif op == "C" and recording is not None:
            from ose.recording.component import MAX_TICK_BYTES
            recording_bytes += len(body)
            if recording_bytes > MAX_TICK_BYTES:
                raise ValueError("component exceeded recording callback budget")
            name, payload = ipc.unpack_port(body)
            recording.emit(name, payload)
        elif op == "P":
            port, payload, app_id = unpack_publication(body)
            services.scheduler.authorize_output(port)
            yield port, payload, app_id
        else:
            raise ValueError("component must publish, access inputs or finish its callback")


async def stop_component(process):
    async def discard_stdout():
        # A cancelled callback can leave a paused, full StreamReader behind. Drain it
        # while reaping so neither Process.wait() nor pipe cleanup gets stuck.
        try:
            while True:
                op, body = await ipc.receive(process.stdout)
                if op == 'L' and hasattr(process, 'logs'):
                    process.logs.emit(body)
        except (EOFError, asyncio.IncompleteReadError, ValueError):
            # Malformed/truncated output during failure still needs draining.
            while await process.stdout.read(65536):
                pass
    draining = asyncio.create_task(discard_stdout())
    async def stop():
        if process.returncode is None:
            await ipc.send(process.stdin, "X")
        await process.wait()
        await asyncio.shield(draining)
    try:
        # Include writer.drain() in the grace deadline, even for a full pipe.
        await asyncio.wait_for(stop(), .5)
    except (ConnectionError, asyncio.TimeoutError):
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(stop_after_signal(process, draining), 1)
        except asyncio.TimeoutError:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            await draining
    finally:
        process.stdin.close()
        if not draining.done():
            draining.cancel()
        await asyncio.gather(draining, return_exceptions=True)


async def stop_after_signal(process, draining):
    await process.wait()
    await asyncio.shield(draining)
