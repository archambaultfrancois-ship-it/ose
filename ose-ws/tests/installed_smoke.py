"""Verify the built wheel outside the checkout, with an unavailable OSE runtime."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile


def main():
    project = Path(__file__).resolve().parents[1]
    wheel = next((project / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        index = archive.read("ose_ws/static/index.html").decode()
        assert '<html lang="en">' in index
        assert "https://" not in index
        assert any(name.startswith("ose_ws/static/assets/") and name.endswith(".js") for name in archive.namelist())
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        installed = root / "site"
        subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-deps",
                        "--target", str(installed), str(wheel)], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        env = dict(os.environ, PYTHONPATH=str(installed) + os.pathsep + str(project.parent / "sdl/runtime/python"))
        env.pop("OSE_PARAMETER_TOKEN", None)
        config = root / "console.json"
        subprocess.run([sys.executable, "-m", "ose_ws.cli", "init", "--config", str(config)],
                       cwd=root, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        subprocess.run([sys.executable, "-m", "ose_ws.cli", "check", "--config", str(config)],
                       cwd=root, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = subprocess.Popen([sys.executable, "-m", "ose_ws.cli", "--config", str(config), "--port", str(port)],
                                  cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/health/ready", timeout=.5) as response:
                        assert json.load(response)["ready"]
                    break
                except (URLError, OSError):
                    if time.monotonic() > deadline or server.poll() is not None:
                        raise AssertionError("The installed console did not become ready.")
                    time.sleep(.05)
            with urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
                assert response.read().decode() == index
            token = (root / ".ose-ws/access.token").read_text().strip()
            login = Request(f"http://127.0.0.1:{port}/api/v1/auth/login", data=json.dumps({"token": token}).encode(),
                            headers={"Content-Type": "application/json"})
            with urlopen(login, timeout=3) as response:
                assert json.load(response)["actor"] == "local"
                cookie = response.headers["Set-Cookie"].split(";", 1)[0]
            request = Request(f"http://127.0.0.1:{port}/api/v1/application", headers={"Cookie": cookie})
            with urlopen(request, timeout=3) as response:
                assert json.load(response)["connected"] is False
        finally:
            server.terminate()
            output, _ = server.communicate(timeout=15)
            if server.returncode != 0:
                raise AssertionError(f"Installed console exited with {server.returncode}: {output.decode()}")
    print("Installed wheel smoke test passed: embedded English frontend, login, disconnected startup and graceful exit.")


if __name__ == "__main__":
    main()
