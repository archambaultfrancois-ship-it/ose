"""Start Vibration with its parameter sidecar, edit files and stop the launcher."""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FRAMEWORK = Path(os.environ.get("FRAMEWORK_ROOT", ROOT / "deps/sdl-ose")).resolve()


def observations(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.startswith('{"event": "vibration"')]


class LaunchSyncTests(unittest.IsolatedAsyncioTestCase):
    async def until(self, predicate, process, log):
        async def wait():
            while not predicate():
                self.assertIsNone(process.returncode, log.read_text())
                await asyncio.sleep(.05)
        await asyncio.wait_for(wait(), 45)

    async def stop(self, process):
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 12)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def exercise(self, task):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            parameters = directory / "parameter files"
            path = "machines/1/detection"
            file = parameters / (path + ".json")
            file.parent.mkdir(parents=True)
            file.write_text(json.dumps({path: {"rms_threshold": .01}}))
            log = directory / "launcher.log"
            events = log if task == "run" else ROOT / "build/distributed-logs/observer.log"
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            env = dict(os.environ, FRAMEWORK_ROOT=str(FRAMEWORK),
                       OSE_PARAMETER_TOKEN="test-wrong-inherited-token", SDL_ROOT=str(FRAMEWORK / "sdl"),
                       PYTHONPATH=str(FRAMEWORK / "ose/src"))
            with log.open("w") as stream:
                process = await asyncio.create_subprocess_exec(sys.executable, "tools/vibration.py", task,
                    "--scenario", "normal", "--duration", "60", "--params-sync", str(parameters),
                    "--port", str(port), cwd=ROOT, env=env, stdout=stream, stderr=stream,
                    start_new_session=True)
                children = []
                try:
                    await self.until(lambda: len(list(parameters.rglob("*.json"))) == 4, process, log)
                    await self.until(lambda: any(row["machine_id"] == 1 and row["state"] == 1
                                                for row in observations(events)), process, log)
                    count = len(observations(events))
                    file.write_text(json.dumps({path: {"rms_threshold": 1.}}))
                    await self.until(lambda: any(row["machine_id"] == 1 and row["state"] == 0
                                                for row in observations(events)[count:]), process, log)
                    # An application-side write must update the same file.
                    plan = json.loads((ROOT / "build/plan.json").read_text())
                    client_env = dict(env, OSE_PARAMETER_TOKEN=plan["parameter_clients"]["operator"]["token"])
                    options = [sys.executable, "-m", "ose.cli", "parameter", "set", path, "--file", "-",
                        "--client", "operator", "--host", "127.0.0.1", "--port", str(port)]
                    if task == "distributed-local":
                        options += ["--distributed", "--insecure"]
                    writer = await asyncio.create_subprocess_exec(*options, cwd=ROOT, env=client_env,
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    _, stderr = await asyncio.wait_for(writer.communicate(b'{"rms_threshold": 2.0}'), 15)
                    self.assertEqual(writer.returncode, 0, stderr.decode())
                    await self.until(lambda: json.loads(file.read_text())[path]["rms_threshold"] == 2., process, log)
                    # All direct children, including the sidecar, must be reaped.
                    child_file = Path(f"/proc/{process.pid}/task/{process.pid}/children")
                    if child_file.exists():
                        children = [int(pid) for pid in child_file.read_text().split()]
                        self.assertGreaterEqual(len(children), 2)
                    process.send_signal(signal.SIGTERM)
                    await asyncio.wait_for(process.wait(), 12)
                    self.assertEqual(process.returncode, 130, log.read_text())
                    for child in children:
                        with self.assertRaises(ProcessLookupError):
                            os.kill(child, 0)
                finally:
                    await self.stop(process)

    async def test_local_launch_with_sync(self):
        await self.exercise("run")

    async def test_distributed_launch_with_sync(self):
        await self.exercise("distributed-local")

    async def test_make_launch_stops_sync_at_duration_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "parameter files"
            env = dict(os.environ, FRAMEWORK_ROOT=str(FRAMEWORK))
            process = await asyncio.create_subprocess_exec("make", "run", "PARAMS_SYNC=1",
                "PARAMS_DIR=" + str(directory), "SCENARIO=normal", "DURATION=3", cwd=ROOT, env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), 90)
                self.assertEqual(process.returncode, 0, stderr.decode())
                self.assertEqual(len(list(directory.rglob("*.json"))), 4)
                self.assertIn("watching 4 parameters", stderr.decode())
            finally:
                await self.stop(process)
