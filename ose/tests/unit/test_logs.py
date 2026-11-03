import io
import json
import struct
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from ose.cli import create_parser
from ose.execution.component import ComponentSession
from ose.execution.ipc import frame
from ose.execution.logs import ComponentLogs, LogStore, log_body, TRUNCATION_SUFFIX


def entry(message='message', level='INFO', logger='runtime'):
    return dict(level=level, logger=logger, message=message, time_ns='1730000000000000000')


class LogTests(unittest.TestCase):
    def test_python_formatting_reports_errors_before_depositing(self):
        output = io.BytesIO()
        session = ComponentSession(io.BytesIO(frame('I', b'{}')), output, auto_ready=False)
        session.logf('FATAL', 'motor=%d rms=%.2f %%', 7, 1.25, logger='detection')
        raw = output.getvalue()
        queue = ComponentLogs(); queue.emit(raw[5:])
        self.assertEqual(queue.drain()[0]['message'], 'motor=7 rms=1.25 %')
        for pattern, arguments in [('%d', ('text',)), ('%s %s', ('one',)), ('%c', (0,))]:
            with self.subTest(pattern=pattern), self.assertRaises((TypeError, ValueError)):
                session.logf('INFO', pattern, *arguments)
        self.assertEqual(output.getvalue(), raw)

    def test_plain_and_formatted_messages_truncate_with_valid_utf8_and_marker(self):
        cut = 8192 - len(TRUNCATION_SUFFIX.encode())
        for message in ('a' * 8192, 'a' * 8193, 'é' * 5000, 'a' * (cut - 1) + '€' + 'b' * 20, '🙂' * 3000):
            with self.subTest(bytes=len(message.encode())):
                queue = ComponentLogs()
                queue.emit(log_body('FATAL', message))
                result, = queue.drain()
                self.assertLessEqual(len(result['message'].encode()), 8192)
                if len(message.encode()) <= 8192:
                    self.assertEqual(result['message'], message)
                else:
                    self.assertTrue(result['message'].endswith(TRUNCATION_SUFFIX))
                    self.assertTrue(message.startswith(result['message'][:-len(TRUNCATION_SUFFIX)]))
                    self.assertNotIn('\ufffd', result['message'])
                output = io.BytesIO()
                session = ComponentSession(io.BytesIO(frame('I', b'{}')), output, auto_ready=False)
                session.logf('FATAL', '%s', message)
                queue.emit(output.getvalue()[5:])
                formatted, = queue.drain()
                self.assertEqual(formatted['message'], result['message'])

    def test_levels_utf8_and_python_sdk_frames(self):
        output = io.BytesIO()
        session = ComponentSession(io.BytesIO(frame('I', b'{}')), output, auto_ready=False)
        session.log('Fatal', 'Échec moteur', 'detection')
        raw = output.getvalue()
        self.assertEqual(struct.unpack('>I', raw[:4])[0], len(raw) - 4)
        self.assertEqual(raw[4:5], b'L')
        queue = ComponentLogs(); queue.emit(raw[5:])
        record, = queue.drain()
        self.assertEqual(record['level'], 'FATAL')
        self.assertEqual(record['message'], 'Échec moteur')
        self.assertEqual(record['logger'], 'detection')
        self.assertEqual(log_body('CRITICAL', 'x')[0], 50)
        self.assertEqual(log_body('FATAL', 'x')[0], 60)

    def test_bounded_queue_reports_drops_and_drains_batches(self):
        queue = ComponentLogs()
        for _ in range(200): queue.emit(log_body('INFO', 'event'))
        self.assertEqual(len(queue.pending), 128)
        self.assertEqual(queue.dropped, 72)
        self.assertEqual(len(queue.drain()), 32)
        while queue.pending: queue.drain()
        self.assertEqual(queue.bytes, 0)
        for _ in range(100): queue.emit(log_body('INFO', 'x' * 8192))
        self.assertLessEqual(queue.bytes, 256 * 1024)
        self.assertLessEqual(len(json.dumps(queue.drain()).encode()), 512 * 1024)

    def test_invalid_records_are_rejected_and_batches_are_atomic(self):
        for level, message, logger in [('TRACE', 'x', ''), ('INFO', '', ''),
                                       ('INFO', 'x', 'a' * 201), ('INFO', 'x\0y', '')]:
            with self.subTest(level=level, size=len(message)), self.assertRaises(ValueError):
                log_body(level, message, logger)
        for data in (b'', b'\x3c\x00', b'\x3c\x00\x05x', b'\x3c\x00\x00\xff'):
            with self.assertRaises((ValueError, UnicodeError)): ComponentLogs().emit(data)
        store = LogStore()
        with self.assertRaises(ValueError): store.append('source', 'generation', [entry(), entry(level='INVALID')])
        self.assertEqual(store.sequence, 0)
        with self.assertRaises(ValueError):
            store.append('source', 'generation', [entry('x' * 8193)])

    def test_filtering_pagination_scopes_and_generation(self):
        store = LogStore('epoch')
        store.append('sensor_1', 'generation-one', [entry('normal'), entry('Overheat', 'FATAL')])
        store.append('secret', 'generation-two', [entry('Overheat', 'FATAL')])
        result = store.query(components=['sensor_*'], levels=['FATAL'], search='overHEAT', logger='run*')
        self.assertEqual([row['message'] for row in result['items']], ['Overheat'])
        self.assertEqual(result['items'][0]['generation'], 'generation-one')
        self.assertEqual(len(store.query(scopes=['sensor_*'])['items']), 2)
        first = store.query(after='0', limit=1)
        self.assertEqual(first['cursor'], '1')
        self.assertTrue(first['more'])
        second = store.query(after=first['cursor'], limit=1, levels=['FATAL'])
        self.assertEqual(second['items'][0]['sequence'], '2')
        empty = store.query(after='2', components=['absent'])
        self.assertEqual(empty['items'], [])
        self.assertEqual(empty['cursor'], '3')

    def test_retention_epoch_restart_and_reply_size_are_bounded(self):
        store = LogStore('new', capacity=2)
        store.append('source', 'g', [entry(str(i)) for i in range(4)])
        self.assertEqual(store.query()['retained'], 2)
        self.assertTrue(store.query(after='0')['truncated'])
        self.assertTrue(store.query(after='999', epoch='old')['truncated'])
        big = LogStore(byte_budget=16384)
        for _ in range(10): big.append('source', 'g', [entry('a' * 8192)])
        self.assertLessEqual(big.bytes, 16384)
        store = LogStore()
        for _ in range(10): store.append('source', 'g', [entry('\u0001' * 8192)] * 32)
        reply = store.query(after='0')
        self.assertLess(len(json.dumps(reply).encode()), 200 * 1024)
        self.assertTrue(reply['more'])
        self.assertTrue(store.query(after=reply['cursor'])['items'])

    def test_cli_and_application_workflow_accept_fatal(self):
        parser = create_parser()
        for command in (['logs'], ['app', 'logs']):
            args = parser.parse_args([*command, 'sensor_*', '--level', 'FATAL', '-f'])
            self.assertEqual(args.level, ['FATAL'])
            self.assertTrue(args.follow)


class CLogFormatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sdk = Path(__file__).resolve().parents[2] / 'sdk/c'
        self.compiler = os.environ.get('CC', 'cc')

    def compile(self, source, *, syntax=False):
        path = self.root / 'format.c'
        path.write_text(source)
        command = [self.compiler, '-std=c99', '-Wall', '-Wextra', '-Werror=format', '-I', str(self.sdk), str(path)]
        command += ['-fsyntax-only'] if syntax else [str(self.sdk / 'ose_component.c'), '-lm', '-o', str(self.root / 'format')]
        return subprocess.run(command, capture_output=True, text=True, timeout=30)

    def test_public_printf_signature_checks_argument_types(self):
        correct = self.compile('''#include "ose_callbacks.h"
void check(OseContext *ctx) {
    ose_logf(ctx, OSE_LOG_INFO, "test", "%u %.2f %s", 7U, 1.25, "ok");
    ose_logf(ctx, OSE_LOG_FATAL, "test", "%d %zu", 42, (size_t)3);
}
''', syntax=True)
        self.assertEqual(correct.returncode, 0, correct.stderr)
        for call in ('ose_logf(ctx, OSE_LOG_INFO, "test", "%d", "wrong")',
                     'ose_logf(ctx, OSE_LOG_FATAL, "test", "%s", 42)'):
            result = self.compile('#include "ose_callbacks.h"\nvoid check(OseContext *ctx) { (void)ctx; ' + call + '; }', syntax=True)
            self.assertNotEqual(result.returncode, 0, 'Compiler accepted a mismatched printf argument')
            self.assertIn('format', result.stderr)

    def test_only_formatted_context_service_is_public(self):
        for name in ('ose_log', 'ose_vlogf', 'ose_log_message', 'ose_logf_message',
                     'ose_vlogf_message', 'ose_internal_log', 'ose_internal_vlogf'):
            with self.subTest(name=name):
                result = self.compile('#include "ose_callbacks.h"\nvoid check(void) { (void)&' + name + '; }', syntax=True)
                self.assertNotEqual(result.returncode, 0, 'Private or removed log function is public: ' + name)

    def test_c_internal_deposits_truncate_with_valid_utf8(self):
        result = self.compile('''#include "ose_component_internal.h"
#include <string.h>
static int forward(OseLogLevel level, const char *logger, const char *format, ...) {
    va_list args; int ok;
    va_start(args, format); ok = ose_internal_vlogf(level, logger, format, args); va_end(args);
    return ok;
}
int main(void) {
    char utf8[8197];
    if (!forward(OSE_LOG_INFO, "test", "id=%u rms=%.2f %s", 7U, 1.25, "ok")) return 1;
    if (!forward(OSE_LOG_FATAL, "forward", "value=%d", 42)) return 2;
    if (!forward(OSE_LOG_INFO, "test", "%08193d", 1)) return 3;
    if (forward(OSE_LOG_INFO, "test", "%c", 0)) return 4;
    if (!forward(OSE_LOG_INFO, "test", "%08192d", 1)) return 5;
    memset(utf8, 'a', sizeof(utf8));
    utf8[UTF8_SPLIT] = (char)0xe2; utf8[UTF8_SPLIT + 1] = (char)0x82; utf8[UTF8_SPLIT + 2] = (char)0xac;
    utf8[sizeof(utf8) - 1] = 0;
    if (!ose_internal_log(OSE_LOG_INFO, "test", utf8)) return 6;
    if (!forward(OSE_LOG_INFO, "test", "%s", utf8)) return 7;
    return 0;
}
'''.replace('UTF8_SPLIT', str(8192 - len(TRUNCATION_SUFFIX.encode()) - 1)))
        self.assertEqual(result.returncode, 0, result.stderr)
        process = subprocess.run([str(self.root / 'format')], capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        stream, queue = io.BytesIO(process.stdout), ComponentLogs()
        while stream.tell() < len(process.stdout):
            size, = struct.unpack('>I', stream.read(4))
            body = stream.read(size)
            self.assertEqual(body[:1], b'L')
            queue.emit(body[1:])
        rows = queue.drain()
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]['message'], 'id=7 rms=1.25 ok')
        self.assertEqual(rows[1]['message'], 'value=42')
        self.assertEqual(rows[1]['level'], 'FATAL')
        self.assertTrue(rows[2]['message'].endswith(TRUNCATION_SUFFIX))
        self.assertEqual(len(rows[2]['message'].encode()), 8192)
        self.assertEqual(len(rows[3]['message'].encode()), 8192)
        self.assertFalse(rows[3]['message'].endswith(TRUNCATION_SUFFIX))
        for row in rows[4:]:
            self.assertEqual(row['message'], 'a' * (8192 - len(TRUNCATION_SUFFIX.encode()) - 1) + TRUNCATION_SUFFIX)
            self.assertLessEqual(len(row['message'].encode()), 8192)
