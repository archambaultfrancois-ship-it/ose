"""Exercise the installed wheel outside the checkout with OSE unavailable.

Usage: python tests/check_wheel.py dist/ose_ws-0.1.0-py3-none-any.whl
OSE and the backend dependencies must already be installed in this interpreter.
"""
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def check(wheel):
    with tempfile.TemporaryDirectory(prefix="ose-ws-wheel-") as temporary:
        root = Path(temporary)
        target, application = root / "installed", root / "application"
        application.mkdir()
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps",
                        "--target", str(target), str(wheel)], check=True)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(target), environment.get("PYTHONPATH"))))
        for name in ("OSE_WS_STATE_DIR", "OSE_WS_HOST", "OSE_WS_PORT"):
            environment.pop(name, None)
        command = [str(target / "bin" / "ose-ws")]

        def cli(*arguments):
            return subprocess.run(command + list(arguments), cwd=application, env=environment,
                                  capture_output=True, text=True, timeout=20)

        def expect(result, code):
            assert result.returncode == code, (result.returncode, result.stdout, result.stderr)

        imported = subprocess.run([sys.executable, "-c", "import ose_ws; print(ose_ws.__file__)"],
                                  cwd=application, env=environment, capture_output=True, text=True, check=True)
        assert Path(imported.stdout.strip()).is_relative_to(target), imported.stdout
        expect(cli("init"), 0)
        expect(cli("check"), 0)
        expect(cli("init"), 3)
        configuration = application / "ose-ws.json"
        settings = json.loads(configuration.read_text())
        # Use an absent path regardless of any endpoint files in the checkout.
        settings["application"]["endpoint"] = "missing-endpoint.json"
        configuration.write_text(json.dumps(settings))
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        log = root / "server.log"
        with log.open("w") as output:
            process = subprocess.Popen(command + ["--port", str(port)], cwd=application,
                                       env=environment, stdout=output, stderr=subprocess.STDOUT)
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError(log.read_text())
                    try:
                        with urlopen(base + "/health/ready", timeout=1) as response:
                            assert json.load(response)["ready"]
                        break
                    except URLError:
                        time.sleep(.1)
                with urlopen(base, timeout=3) as response:
                    html = response.read().decode()
                assert '<html lang="en">' in html
                assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
                assert len(assets) >= 2, html
                for asset in assets:
                    with urlopen(base + asset, timeout=3) as response:
                        assert len(response.read()) > 100
                token = (application / ".ose-ws/access.token").read_text().strip()
                request = Request(base + "/api/v1/auth/login",
                                  data=json.dumps({"token": token}).encode(),
                                  headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=3) as response:
                    assert json.load(response)["actor"] == "local"
                    cookie = response.headers["Set-Cookie"].split(";")[0]
                request = Request(base + "/api/v1/application", headers={"Cookie": cookie})
                with urlopen(request, timeout=3) as response:
                    assert json.load(response)["connected"] is False
                # The second process must fail before it can bind the same port.
                expect(cli("--port", str(port)), 3)
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise AssertionError("Server did not shut down within 20 seconds.") from None
            # Uvicorn versions may re-raise SIGTERM after completing shutdown.
            assert process.returncode in (0, -signal.SIGTERM), log.read_text()
            assert "Application shutdown complete." in log.read_text(), log.read_text()

        other = root / "other"
        other.mkdir()
        config = other / "console.json"
        expect(cli("init", "--config", str(config)), 0)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            expect(cli("--config", str(config), "--port", str(listener.getsockname()[1])), 3)
    print("Installed wheel passed: embedded frontend, authentication, disconnected OSE, ownership, port failure and graceful shutdown.")


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve())
