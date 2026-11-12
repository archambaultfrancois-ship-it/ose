"""Capture real sensor outputs and reproduce their alarms through ose replay."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FRAMEWORK = Path(os.environ.get("FRAMEWORK_ROOT", ROOT.parent)).resolve()
sys.path.insert(0, str(FRAMEWORK / "ose/src"))

from ose.codec.sdl2 import SDL2
from ose.recording.replay import recording_reader
from ose.recording.recorder import Recorder


def observations(text):
    return [json.loads(line) for line in text.splitlines() if line.startswith('{"event": "vibration"')]


def ordered(rows):
    return sorted(rows, key=lambda row: (row["machine_id"], row["sequence"]))


class ReplayApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, *options):
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, "tools/vibration.py", *map(str, options), "--framework-root", str(FRAMEWORK)],
            cwd=ROOT, env=os.environ, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    async def test_captured_signals_reproduce_alarms_in_simulation_and_live_modes(self):
        codec = SDL2(FRAMEWORK / "sdl")
        reader = recording_reader(codec)
        await self.invoke("build")
        environment = dict(os.environ, SDL_ROOT=str(FRAMEWORK / "sdl"), PYTHONPATH=str(FRAMEWORK / "ose/src"))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            capture = directory / "signals.pcapng"
            with (directory / "live.log").open("w+") as log:
                process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "run",
                    "--plan", str(ROOT / "build/plan.json"), "--duration", "15", cwd=ROOT,
                    env=environment, stdout=asyncio.subprocess.PIPE, stderr=log)
                recorder = None
                try:
                    announcement = await asyncio.wait_for(process.stdout.readline(), 30)
                    self.assertTrue(announcement, (directory / 'live.log').read_text())
                    running = json.loads(announcement)
                    self.assertTrue(running["running"])
                    recorder = Recorder(running["host"], running["port"])
                    await recorder.connect()
                    recorder.selected = {"sensor_1.signal": None, "sensor_2.signal": None}
                    await recorder.start(capture)
                    await asyncio.sleep(2)
                    status = await recorder.stop()
                    self.assertIsNone(status["error"])
                    # Let the observer finish all downstream processing of the
                    # last captured publications before stopping the live run.
                    await asyncio.sleep(.2)
                finally:
                    if recorder is not None:
                        await recorder.close()
                    if process.returncode is None:
                        process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 10)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                log.seek(0)
                live = observations(log.read())
            keys = set()
            for record in reader(capture).iter_records():
                fields = record.decode().fields
                keys.add((fields["machine_id"], fields["sequence"]))
            self.assertGreaterEqual(len(keys), 30)
            expected = [row for row in live if (row["machine_id"], row["sequence"]) in keys]
            self.assertEqual(len(expected), len(keys))
            self.assertTrue(any(row["state"] for row in expected if row["machine_id"] == 1))
            self.assertTrue(all(row["state"] == 0 for row in expected if row["machine_id"] == 2))
            reference = None
            for workers in (1, 4):
                internal = directory / 'replayed-inputs.mcap'
                capture_options = (['--record', 'analyzer_*.record.dsp.input', '--record-file', internal]
                                   if workers == 4 else [])
                result = await self.invoke("replay", "--recording", capture, "--duration", 3,
                                           "--workers", workers, *capture_options)
                self.assertEqual(ordered(observations(result.stderr)), ordered(expected))
                summary = json.loads(result.stdout.splitlines()[-1])
                self.assertEqual(summary["replay"]["message_count"], len(keys))
                plan = json.loads((ROOT / "build/plan.json").read_text())
                self.assertEqual(set(plan["components"]), {"sensor_1", "sensor_2", "analyzer_1", "analyzer_2", "alarm", "observer"})
                self.assertEqual(set(plan["shared_parameters"]), {"machines/1/detection", "machines/2/detection", "machines/1/sensor", "machines/2/sensor", "observer/display"})
                trace = (ROOT / "build/replay/trace.jsonl").read_bytes()
                if reference is None:
                    reference = trace
                self.assertEqual(trace, reference)
                if workers == 4:
                    recorded = list(reader(internal).iter_records())
                    self.assertEqual(len(recorded), len(keys))
                    self.assertEqual({row.channel.topic for row in recorded},
                                     {'analyzer_1.record.dsp.input', 'analyzer_2.record.dsp.input'})
                    self.assertEqual({(row.decode().fields['machine_id'], row.decode().fields['sequence'])
                                      for row in recorded}, keys)
            repeated = await self.invoke("replay", "--recording", capture, "--duration", 5, "--loops", 2)
            self.assertEqual(ordered(observations(repeated.stderr)), ordered(expected * 2))
            self.assertEqual(json.loads(repeated.stdout.splitlines()[-1])["completed_loops"], 2)
            for profile, speed in (("local", 1), ("cluster", 2)):
                with self.subTest(profile=profile):
                    await self.replay_live(capture, directory, environment, expected, profile, speed)

    async def replay_live(self, capture, directory, environment, expected, profile, speed):
        await self.invoke("replay-build", "--profile", profile)
        plan = ROOT / "build/plan.json"
        processes = []
        log_path = directory / (profile + ".log")
        with log_path.open("w") as log:
            async def launch(*options):
                process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", *map(str, options),
                    cwd=ROOT, env=environment, stdout=asyncio.subprocess.PIPE, stderr=log)
                processes.append(process)
                return process
            try:
                disabled = ['--disable-component', 'sensor_1', '--disable-component', 'sensor_2']
                runtime = await launch("run", "--plan", plan, *disabled) if profile == "local" else await launch(
                    "serve", "--plan", plan, "--port", 0, "--insecure", *disabled)
                address = json.loads(await asyncio.wait_for(runtime.stdout.readline(), 20))
                if profile == "cluster":
                    for name in json.loads(plan.read_text())["components"]:
                        await launch("worker", "--plan", plan, "--node", name,
                                     "--port", address["port"], "--insecure")
                    from ose.transport.secure import connect
                    control = await connect(address["host"], address["port"], 1024 * 1024)
                    try:
                        async def ready():
                            while True:
                                status, _ = await control.request("status")
                                if status["enabled"]:
                                    return
                                await asyncio.sleep(.05)
                        await asyncio.wait_for(ready(), 20)
                    finally:
                        await control.close()
                arguments = ["replay", capture, "--mode", "realtime", "--plan", plan,
                    "--host", address["host"], "--port", address["port"], "--speed", speed,
                    "--map", "sensor_1.signal=sensor_1.signal", "--map", "sensor_2.signal=sensor_2.signal"]
                if profile == "cluster":
                    arguments += ["--insecure"]
                replayer = await launch(*arguments)
                stdout, _ = await asyncio.wait_for(replayer.communicate(), 30)
                self.assertEqual(replayer.returncode, 0, stdout.decode() + "\n" + log_path.read_text())
                summary = json.loads(stdout)
                self.assertEqual(summary["published"], len(expected))
                self.assertEqual(summary["refused"], 0)
                async def processed():
                    while len(observations(log_path.read_text())) < len(expected):
                        await asyncio.sleep(.02)
                try:
                    await asyncio.wait_for(processed(), 10)
                except asyncio.TimeoutError:
                    detail = ""
                    if profile == "cluster":
                        control = await connect(address["host"], address["port"], 1024 * 1024)
                        try:
                            status, _ = await control.request("status")
                            detail = json.dumps(status)
                        finally:
                            await control.close()
                    self.fail(f"received {len(observations(log_path.read_text()))}/{len(expected)} observations; "
                              f"status={detail}; logs={log_path.read_text()}")
                self.assertEqual(ordered(observations(log_path.read_text())), ordered(expected))
            finally:
                for process in reversed(processes):
                    if process.returncode is None:
                        process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 10)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
