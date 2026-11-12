"""Launch isolated adapter/component processes and coordinate readiness."""
from ose.execution.plan import read_plan
from ose.modeling.activation import activation_order
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
from ose.execution.broker import Broker


async def run(plan_path, duration=None, port=0, *, working_directory=None, record=None, record_file=None,
              record_from=None, record_until=None, params_sync=None, disabled=()):
    plan_path = Path(plan_path).resolve()
    plan = read_plan(plan_path)
    activation_order(plan)
    from ose.recording.batch import destination, LiveCapture, CaptureWindow
    CaptureWindow(record, record_from, record_until)
    destination(record, record_file, protected=[plan_path, Path(plan['build_dir']) / 'endpoint.json',
                                                Path(plan['build_dir']) / 'last-status.json'])
    from ose.execution.identity import identify
    identify("broker", plan)
    broker = Broker(plan, disabled=disabled)
    children = []
    waiting = []
    capture = None
    capture_task = None
    from ose.execution.launch import Processes, start_sync
    sidecars = Processes()
    sync_task = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await broker.start(port)
        endpoint = {"host": "127.0.0.1", "port": broker.port}
        (Path(plan["build_dir"]) / "endpoint.json").write_text(json.dumps(endpoint))
        env = dict(os.environ)
        source_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
        options = ["--working-directory", str(Path(working_directory).resolve())] if working_directory is not None else []
        for name in plan["components"]:
            # Keep terminal interrupts at this supervisor, including while an
            # adapter is importing modules before installing its own handlers.
            child = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "adapter",
                "--plan", str(plan_path), "--node", name, "--port", str(broker.port), *options, env=env,
                start_new_session=True)
            children.append(child)
            waiting.append(asyncio.create_task(child.wait()))

        async def ready():
            while broker.ready != set(plan["components"]):
                broker.readiness.clear()
                await broker.readiness.wait()

        startup = asyncio.create_task(ready())
        stopped = asyncio.create_task(stop.wait())
        waiting.extend((startup, stopped))
        done, _ = await asyncio.wait(waiting, timeout=20, return_when=asyncio.FIRST_COMPLETED)
        if stopped in done:
            if params_sync:
                raise KeyboardInterrupt
            return
        if any(task in done for task in waiting[:-2]):
            raise RuntimeError("component failed during startup")
        if startup not in done:
            raise RuntimeError("component readiness timeout")
        startup.result()
        if params_sync:
            await start_sync(sidecars, plan_path=plan_path, port=broker.port, **params_sync)
            sync_task = asyncio.create_task(sidecars.monitor())
        broker.recording_ready = True
        capture = LiveCapture(record, record_file, host='127.0.0.1', port=broker.port, sdl_root=plan['sdl_root'],
                              record_from=record_from, record_until=record_until)
        await capture.__aenter__()
        if capture.recorder is not None:
            capture_task = capture.recorder.capture.task
        capture.begin_run()
        broker.enabled = True
        broker.recording_ready = False
        print(json.dumps({"running": True, **endpoint}), flush=True)
        done, _ = await asyncio.wait([stopped, *waiting[:-2], *([capture_task] if capture_task else []),
                                     *([sync_task] if sync_task else [])], timeout=duration,
                                     return_when=asyncio.FIRST_COMPLETED)
        if sync_task is not None and sync_task in done:
            sync_task.result()
        if capture_task is not None and capture_task in done:
            raise ValueError(f"batch recording stopped: {capture.recorder.capture.error or 'unexpected end'}")
        if stopped not in done and done:
            raise RuntimeError("component failed; stopping OSE")
        if stopped in done and params_sync:
            raise KeyboardInterrupt
    finally:
        broker.enabled = False
        broker.recording_ready = False
        if sync_task is not None:
            sync_task.cancel()
            await asyncio.gather(sync_task, return_exceptions=True)
        await sidecars.close()
        capture_error = None
        if capture is not None:
            try:
                await capture.__aexit__(*sys.exc_info())
            except BaseException as error:
                capture_error = error
        for child in children:
            if child.returncode is None:
                child.terminate()
        for child in children:
            try:
                await asyncio.wait_for(child.wait(), 3)
            except asyncio.TimeoutError:
                child.kill()
                await child.wait()
        for task in waiting:
            task.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)
        snapshot = broker.snapshot() if hasattr(broker, "udp") else {}
        # Application measurements are RAM-only, including diagnostic shutdown files.
        snapshot['components'] = {name: {key: value for key, value in metrics.items() if key != 'measurements'}
                                  for name, metrics in snapshot.get('components', {}).items()}
        (Path(plan["build_dir"]) / "last-status.json").write_text(json.dumps(snapshot, indent=2))
        if hasattr(broker, "server"):
            await broker.close()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        if capture_error is not None:
            raise capture_error
