from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from ose.cli import main
from ose.subsystems.cli import initialize
from ose.subsystems.harness import settings


class SubsystemCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = initialize(self.root / 'isolated')

    def call(self, *args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(['subsystem', *args])

    def test_init_does_not_overwrite_existing_sources(self):
        source = self.target / 'components/processor.py'
        source.write_text('custom source')
        self.assertEqual(self.call('init', str(self.target)), 2)
        self.assertEqual(source.read_text(), 'custom source')
        self.assertTrue((self.target / '.gitignore').is_file())

    def test_definition_without_harness_has_build_profile_but_no_passing_tests(self):
        (self.target / 'tests/harness.json').unlink()
        self.assertEqual(settings(self.target, 'standalone')[3]['cases'], {})
        self.assertEqual(self.call('test', str(self.target)), 2)

    def test_exit_codes_and_junit_distinguish_failures_from_errors(self):
        for status, code, xml_tag in (('passed', 0, None), ('failed', 1, 'failure'),
                                      ('error', 3, 'error')):
            with self.subTest(status=status), patch('ose.subsystems.cli.execute_case', return_value=dict(
                    case='nominal', integration_profile='standalone', status=status)):
                destination = self.root / status
                report = destination / 'junit.xml'
                self.assertEqual(self.call('test', str(self.target), '--build-dir', str(destination),
                    '--report', 'junit', '--output', str(report)), code)
                suite = ET.parse(report).getroot()
                case = suite.find('testcase')
                self.assertEqual(len(case), 0 if xml_tag is None else 1)
                if xml_tag:
                    self.assertIsNotNone(case.find(xml_tag))

    def test_bad_case_or_workers_fail_before_any_build(self):
        with patch('ose.subsystems.cli.execute_case') as execute:
            self.assertEqual(self.call('test', str(self.target), '--case', 'missing'), 2)
            self.assertEqual(self.call('test', str(self.target), '--workers', '0'), 2)
            self.assertEqual(self.call('test', str(self.target), '--report', 'junit'), 2)
            execute.assert_not_called()

    def test_build_error_is_reported_in_junit_and_does_not_become_an_assertion(self):
        with patch('ose.subsystems.cli.execute_case', side_effect=ValueError('invalid contract')):
            report = self.root / 'report.xml'
            self.assertEqual(self.call('test', str(self.target), '--build-dir', str(self.root / 'artifacts'),
                '--report', 'junit', '--output', str(report)), 2)
            self.assertEqual(ET.parse(report).getroot().get('errors'), '1')
            results = json.loads((self.root / 'artifacts/results.json').read_text())
            self.assertEqual(results['results'][0]['status'], 'configuration_error')
