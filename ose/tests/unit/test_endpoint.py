"""Endpoint discovery follows the active launcher without weakening TLS policy."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ose.transport.endpoint import publish, resolve


class EndpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.options = dict(endpoint=str(self.root / 'endpoint.json'))

    def test_mode_and_port_follow_successive_launches(self):
        for port, distributed in ((1234, False), (2345, True), (3456, False)):
            publish(self.root, port, distributed=distributed, insecure=distributed)
            endpoint = resolve(self.options)
            self.assertEqual((endpoint.host, endpoint.port, endpoint.distributed),
                             ('127.0.0.1', port, distributed))
            self.assertIsNone(endpoint.context)
            self.assertEqual([p.name for p in self.root.iterdir()], ['endpoint.json'])

    def test_failed_publication_preserves_previous_endpoint_and_cleans_temporary(self):
        publish(self.root, 1234)
        with patch('ose.transport.endpoint.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                publish(self.root, 2345, distributed=True, insecure=True)
        self.assertEqual(resolve(self.options).port, 1234)
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_metadata_cannot_downgrade_explicit_tls_credentials(self):
        publish(self.root, 1234, distributed=True, insecure=True)
        context = object()
        with patch('ose.transport.endpoint.contexts', return_value=(None, context)) as contexts:
            endpoint = resolve(dict(self.options, tls_ca='ca', tls_cert='cert', tls_key='key'))
        contexts.assert_called_once_with('ca', 'cert', 'key', insecure=False, hosts=['127.0.0.1'])
        self.assertIs(endpoint.context, context)
        with self.assertRaisesRegex(ValueError, 'tls-ca'):
            resolve(dict(self.options, distributed=True))

    def test_malformed_metadata_and_remote_plaintext_are_rejected(self):
        for value in ([], dict(host='localhost', port=True),
                      dict(host='localhost', port=1234, distributed='false'),
                      dict(host='localhost', port=1234, insecure=True),
                      dict(host='192.0.2.1', port=1234, distributed=True, insecure=True),
                      dict(host='localhost', port=1234, distributed=True)):
            with self.subTest(value=value):
                Path(self.options['endpoint']).write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    resolve(self.options)

    def test_explicit_address_ignores_endpoint_file(self):
        endpoint = resolve(dict(self.options, host='localhost', port=4321))
        self.assertEqual((endpoint.host, endpoint.port, endpoint.distributed), ('localhost', 4321, False))
