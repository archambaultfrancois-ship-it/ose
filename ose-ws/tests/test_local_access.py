"""Direct local browser access still uses isolated sessions and CSRF protection."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ose_ws.cli import initialize
from ose_ws.config import read
from ose_ws.server import create_app


class LocalAccessTests(unittest.TestCase):
    def test_automatic_session_csrf_origins_and_websocket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ose-ws.json'
            with redirect_stdout(io.StringIO()):
                initialize(path)
            config = read(path)
            self.assertFalse(Path(config['auth']['token_file']).exists())
            with TestClient(create_app(config), base_url='http://127.0.0.1:8080') as client:
                response = client.get('/api/v1/session')
                self.assertEqual(response.status_code, 200, response.text)
                session = response.json()
                self.assertFalse(session['auth_required'])
                self.assertEqual(session['permissions'], {'admin': ['*']})
                self.assertEqual(client.get('/api/v1/session').json()['csrf'], session['csrf'])
                self.assertEqual(client.post('/api/v1/session/renew').status_code, 403)
                headers = {'X-CSRF-Token': session['csrf'], 'Origin': 'http://127.0.0.1:8080'}
                self.assertEqual(client.post('/api/v1/session/renew', headers=headers).status_code, 200)
                self.assertEqual(client.get('/api/v1/session', headers={'Origin': 'https://other.example'}).status_code, 403)
                self.assertEqual(client.get('/api/v1/session', headers={'Host': 'other.example'}).status_code, 403)
                with client.websocket_connect('ws://127.0.0.1:8080/api/v1/live', headers={'Host': '127.0.0.1:8080', 'Origin': 'http://127.0.0.1:8080'}):
                    pass
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect('ws://127.0.0.1:8080/api/v1/live', headers={'Origin': 'https://other.example'}):
                        pass
                client.cookies.clear()
                self.assertNotEqual(client.get('/api/v1/session').json()['csrf'], session['csrf'])

    def test_tokenless_configuration_refuses_network_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ose-ws.json'
            with redirect_stdout(io.StringIO()):
                initialize(path)
            for host in ('0.0.0.0', '::', '192.168.1.10'):
                with self.subTest(host=host), self.assertRaises(ValueError):
                    read(path, {'listen': {'host': host}})
