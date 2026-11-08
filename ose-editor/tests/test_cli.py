"""Exercise the actual local HTTP process and graceful signal shutdown."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import URLError


class CliTests(unittest.TestCase):
    def test_local_startup_and_sigterm_release_project_lock(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / 'app'
            shutil.copytree(root / 'vibration-watch', project, ignore=shutil.ignore_patterns('build', '.ose-editor', '.ose-ws', '__pycache__'))
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            output = tempfile.TemporaryFile()
            process = subprocess.Popen([sys.executable, '-m', 'ose_editor.cli', '--root', str(project), '--sdl-root', str(root / 'sdl'), '--port', str(port)], stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    token = project / '.ose-editor/access.token'
                    if token.is_file():
                        request = Request(f'http://127.0.0.1:{port}/api/v1/project', headers={'Authorization': 'Bearer ' + token.read_text().strip()})
                        try:
                            with urlopen(request, timeout=.5) as response:
                                self.assertIn(b'model.json', response.read())
                            break
                        except (URLError, OSError):
                            pass
                    time.sleep(.05)
                else:
                    self.fail('Editor did not become ready')
            finally:
                process.terminate()
                process.wait(timeout=15)
                output.seek(0)
                log = output.read().decode()
                output.close()
            self.assertEqual(process.returncode, 0, log)
            from ose_editor.project import Project
            reopened = Project(project)
            reopened.close()
