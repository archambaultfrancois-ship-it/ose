"""Exercise application sensor and threshold changes through the public parameter CLI."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FRAMEWORK = Path(os.environ.get("FRAMEWORK_ROOT", ROOT / "deps/sdl-ose")).resolve()


def observations(path):
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.startswith('{"event": "vibration"')]


class SharedSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, command, environment, *, success=True):
        result = await asyncio.to_thread(subprocess.run, [sys.executable, *map(str, command)],
            cwd=ROOT, env=environment, text=True, capture_output=True, timeout=180)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    async def parameters_shell(self, action, options, environment, data=None):
        alias = 'ose app parameter get' if action == 'get' else 'ose app parameter set'
        script = 'set -eu\nshopt -s expand_aliases\nsource "$APP_ENV"\n' + alias + ' "$@"\n'
        env = dict(environment, APP_ENV=str(ROOT / 'setenv.sh'), OSE_PYTHON=sys.executable)
        env.pop('OSE_PARAMETER_CLIENT', None)
        process = await asyncio.create_subprocess_exec('bash', '--noprofile', '--norc', '-c', script, 'test',
            *map(str, options), cwd=ROOT, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(data), 30)
        self.assertEqual(process.returncode, 0, stderr.decode())
        return json.loads(stdout)

    async def until(self, predicate, processes):
        async def wait():
            while not predicate():
                for process in processes:
                    self.assertIsNone(process.returncode, "application process exited")
                await asyncio.sleep(.05)
        await asyncio.wait_for(wait(), 30)

    async def stop(self, process):
        if process.returncode is None:
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 10)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def test_live_updates_and_replacement_keep_other_machine_unchanged(self):
        for profile in ("local", "cluster"):
            with self.subTest(profile=profile):
                await self.exercise(profile)

    async def exercise(self, profile):
        environment = dict(os.environ, SDL_ROOT=str(FRAMEWORK / "sdl"),
                           PYTHONPATH=str(FRAMEWORK / "ose/src"),
                           OSE_PARAMETER_TOKEN="vibration-watch-local-operator")
        await self.invoke(["tools/vibration.py", "build", "--profile", profile,
                           "--scenario", "normal", "--framework-root", FRAMEWORK], environment)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            plan = json.loads((ROOT / "build/plan.json").read_text())
            # Production cluster clients use mTLS. Give this loopback-only test
            # a token in its temporary plan to exercise the same rights over TCP.
            if profile == "cluster":
                plan["parameter_clients"]["operator"]["token"] = environment["OSE_PARAMETER_TOKEN"]
            plan_path = directory / "plan.json"
            plan_path.write_text(json.dumps(plan))
            log_path = directory / "application.log"
            processes = []
            with log_path.open("w") as log:
                async def launch(*options):
                    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli",
                        *map(str, options), cwd=ROOT, env=environment,
                        stdout=asyncio.subprocess.PIPE, stderr=log)
                    processes.append(process)
                    return process
                try:
                    runtime = await launch("run", "--plan", plan_path) if profile == "local" else await launch(
                        "serve", "--plan", plan_path, "--port", 0, "--insecure")
                    address = json.loads(await asyncio.wait_for(runtime.stdout.readline(), 30))
                    workers = {}
                    if profile == "cluster":
                        for name in plan["components"]:
                            workers[name] = await launch("worker", "--plan", plan_path, "--node", name,
                                "--port", address["port"], "--insecure")
                    await self.until(lambda: {row["machine_id"] for row in observations(log_path)} == {1, 2}, processes)
                    self.assertTrue(all(row["state"] == 0 for row in observations(log_path)))
                    endpoint = ["--host", address["host"], "--port", address["port"], "--plan", plan_path]
                    if profile == "cluster":
                        endpoint += ["--distributed", "--insecure"]
                    path = "machines/1/detection"
                    get = ["-m", "ose.cli", "parameter", "get", path, "--client", "operator", *endpoint]
                    self.assertEqual(json.loads((await self.invoke(get, environment)).stdout), {"rms_threshold": 1.0})
                    saved = None
                    if shutil.which('bash'):
                        shell_environment = dict(environment)
                        if profile == 'local':
                            shell_environment.pop('OSE_PARAMETER_TOKEN', None)
                        saved = await self.parameters_shell('get', endpoint, shell_environment)
                        self.assertEqual(set(saved), {f'machines/{n}/{block}' for n in (1, 2)
                                                      for block in ('sensor', 'detection')})
                        self.assertEqual(await self.parameters_shell('get', ['^machines/1/', *endpoint], shell_environment),
                                         {key: value for key, value in saved.items() if key.startswith('machines/1/')})
                        self.assertEqual(await self.parameters_shell('get', ['--exact', path, *endpoint], shell_environment),
                                         {path: {'rms_threshold': 1.0}})
                    update = ["tools/vibration.py", "set-threshold", "--machine", "1",
                              "--framework-root", FRAMEWORK, *endpoint]
                    rejected = await self.invoke([*update, "--rms-threshold", ".01"],
                        dict(environment, OSE_PARAMETER_TOKEN="wrong-token"), success=False)
                    self.assertIn("invalid parameter client token", rejected.stderr.lower())
                    for invalid in ("0", "-1", "nan", "inf"):
                        result = await self.invoke([*update, "--rms-threshold", invalid], environment, success=False)
                        self.assertIn("invalid threshold", result.stderr)
                    self.assertEqual(json.loads((await self.invoke(get, environment)).stdout), {"rms_threshold": 1.0})
                    await self.invoke([*update, "--rms-threshold", ".01"], environment)
                    await self.until(lambda: any(row["machine_id"] == 1 and row["state"] == 1
                                                for row in observations(log_path)), processes)
                    if saved is not None:
                        count = len(observations(log_path))
                        restored = await self.parameters_shell('set', ['--file', '-', *endpoint], shell_environment,
                                                               json.dumps(saved).encode())
                        self.assertEqual(set(restored['updated']), set(saved))
                        await self.until(lambda: any(row['machine_id'] == 1 and row['state'] == 0
                                                    for row in observations(log_path)[count:]), processes)
                        self.assertEqual(await self.parameters_shell('get', endpoint, shell_environment), saved)
                        settings_tree = directory / 'parameter exports'
                        (settings_tree / 'motors').mkdir(parents=True)
                        for machine in (1, 2):
                            (settings_tree / 'motors' / f'motor-{machine}.json').write_text(json.dumps(
                                {key: value for key, value in saved.items() if key.startswith(f'machines/{machine}/')}))
                        restored = await self.parameters_shell('set', ['--dir', settings_tree, *endpoint], shell_environment)
                        self.assertEqual(set(restored['updated']), set(saved))
                        self.assertEqual(await self.parameters_shell('get', endpoint, shell_environment), saved)
                        count = len(observations(log_path))
                        await self.invoke([*update, '--rms-threshold', '.01'], environment)
                        await self.until(lambda: any(row['machine_id'] == 1 and row['state'] == 1
                                                    for row in observations(log_path)[count:]), processes)
                    if profile == "cluster":
                        old = workers["alarm"]
                        await self.stop(old)
                        processes.remove(old)
                        count = len(observations(log_path))
                        workers["alarm"] = await launch("worker", "--plan", plan_path, "--node", "alarm",
                            "--port", address["port"], "--insecure")
                        await self.until(lambda: any(row["machine_id"] == 1 and row["state"] == 1
                                                    for row in observations(log_path)[count:]), processes)
                    self.assertEqual(json.loads((await self.invoke(get, environment)).stdout), {"rms_threshold": .01})
                    count = len(observations(log_path))
                    await self.invoke([*update, "--rms-threshold", "1000000"], environment)
                    await self.until(lambda: any(row["machine_id"] == 1 and row["state"] == 0
                                                for row in observations(log_path)[count:]), processes)
                    # Change all generation settings through the C typed binding.
                    settings = {"samples": 2048, "sample_rate_hz": 8192, "frequency_hz": 64,
                                "amplitude": 4., "noise": 0., "seed": 47, "ramp_per_second": 0.}
                    sensor_file = directory / "sensor.json"
                    sensor_file.write_text(json.dumps({'machines/1/sensor': settings}))
                    await self.invoke([*update, "--rms-threshold", "1"], environment)
                    count = len(observations(log_path))
                    await self.invoke(["tools/vibration.py", "set-sensor", "--machine", "1", "--file", sensor_file,
                                       "--framework-root", FRAMEWORK, *endpoint], environment)
                    await self.until(lambda: any(row["machine_id"] == 1 and row["state"] == 1 and
                                                 abs(row["rms"] - 8**.5) < 1e-5
                                                 for row in observations(log_path)[count:]), processes)
                    sensor_get = ["-m", "ose.cli", "parameter", "get", "machines/1/sensor", "--client", "operator", *endpoint]
                    self.assertEqual(json.loads((await self.invoke(sensor_get, environment)).stdout), settings)
                    self.assertTrue(all(row["state"] == 0 for row in observations(log_path) if row["machine_id"] == 2))
                finally:
                    for process in reversed(processes):
                        await self.stop(process)

    async def test_parameter_scenario_is_deterministic_across_worker_counts(self):
        environment = dict(os.environ, SDL_ROOT=str(FRAMEWORK / "sdl"), PYTHONPATH=str(FRAMEWORK / "ose/src"))
        await self.invoke(["tools/vibration.py", "build", "--scenario", "normal",
                           "--framework-root", FRAMEWORK], environment)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            scenario = directory / "parameters.json"
            scenario.write_text(json.dumps({"version": 1, "events": [
                {"at": .5, "parameter": "machines/1/detection", "value": {"rms_threshold": .01}},
                {"at": 1., "parameter": "machines/1/detection", "value": {"rms_threshold": 1.}},
                {"at": 1.2, "parameter": "machines/1/sensor", "value": {
                    "samples": 2048, "sample_rate_hz": 8192, "frequency_hz": 64,
                    "amplitude": 4., "noise": .01, "seed": 47, "ramp_per_second": 0.}}
            ]}))
            reference = None
            for workers in (1, 4):
                trace = directory / f"trace-{workers}.jsonl"
                result = await self.invoke(["-m", "ose.cli", "run", "--mode", "simulated", "--duration", "1.5",
                    "--workers", workers, "--scenario", scenario, "--trace", trace], environment)
                if reference is None:
                    reference = trace.read_bytes()
                self.assertEqual(trace.read_bytes(), reference)
                rows = [json.loads(line) for line in result.stderr.splitlines()
                        if line.startswith('{"event": "vibration"')]
                machine_1 = [row for row in rows if row["machine_id"] == 1]
                self.assertTrue(machine_1)
                for row in machine_1:
                    self.assertEqual(row["state"], int(500000000 <= row["time_ns"] < 1000000000 or row["time_ns"] >= 1200000000))
                self.assertTrue(all(row["state"] == 0 for row in rows if row["machine_id"] == 2))
