"""Per-message metadata survives chunking, selections and transformations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from sdl_recording import (MESSAGE_METADATA, RecordingError, RecordingReader,
                           RecordingWriter, convert_recording, transform_recordings)

CAT = 'SDL2|Value{1:uint8:value;}'


class MessageMetadataTests(unittest.TestCase):
   def setUp(self):
      self.temporary = tempfile.TemporaryDirectory()
      self.addCleanup(self.temporary.cleanup)
      self.root = Path(self.temporary.name)

   def capture(self, format):
      path = self.root / ('source.' + format)
      with RecordingWriter(path, format=format, chunk_size=256) as writer:
         channels = [writer.register_channel(topic=name, catalogue=CAT, root_type='Value')
                     for name in ('selected', 'ignored')]
         for i in range(40):
            writer.write_encoded(channels[i % 2], bytes((1, i)), log_time_ns=i,
               sequence=0, metadata={'ose.app_id': str(i)} if i % 3 else None)
      return path

   def test_chunking_selection_and_conversion_keep_metadata_on_the_exact_message(self):
      for format in ('pcapng', 'mcap'):
         path = self.capture(format)
         records = list(RecordingReader(path).iter_records())
         expected = [{'ose.app_id': str(i)} if i % 3 else {} for i in range(40)]
         self.assertEqual([dict(r.metadata) for r in records], expected)
         with self.assertRaises(TypeError):
            records[1].metadata['ose.app_id'] = '99'
         selected = list(RecordingReader(path).iter_records(topics='selected', start_time_ns=4, end_time_ns=20))
         self.assertEqual([dict(r.metadata) for r in selected], expected[4:20:2])
         target = self.root / ('converted-' + format)
         convert_recording(path, target, format='mcap' if format == 'pcapng' else 'pcapng')
         converted = list(RecordingReader(target).iter_records())
         self.assertEqual([dict(r.metadata) for r in converted], expected)
         self.assertEqual([r.payload for r in converted], [r.payload for r in records])

   def test_filtered_split_outputs_reindex_metadata_and_preserve_exact_size_accounting(self):
      for format in ('pcapng', 'mcap'):
         path = self.capture(format)
         output = self.root / ('parts-' + format)
         output.mkdir()
         report = transform_recordings(path, str(output / 'part-{index}') + '.' + format,
            topics=['selected'], split_messages=3)
         records = [r for part in report['outputs'] for r in RecordingReader(part['path']).iter_records()]
         self.assertEqual([r.payload[1] for r in records], list(range(0, 40, 2)))
         self.assertEqual([dict(r.metadata) for r in records],
            [{'ose.app_id': str(i)} if i % 3 else {} for i in range(0, 40, 2)])

   def test_inspect_exposes_message_metadata_without_changing_payload_fields(self):
      path = self.capture('pcapng')
      tool = Path(__file__).resolve().parents[2] / 'tools/recording/sdl-recording'
      for format in ('json', 'text', 'table'):
         result = subprocess.run([sys.executable, str(tool), 'inspect', str(path), '--limit', '2',
                                  '--format', format], env=os.environ, capture_output=True, text=True)
         self.assertEqual(result.returncode, 0, result.stderr)
         self.assertIn('ose.app_id', result.stdout)
         if format == 'json':
            rows = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertNotIn('metadata', rows[0])
            self.assertEqual(rows[1]['metadata'], {'ose.app_id': '1'})

   def test_invalid_or_orphaned_metadata_is_rejected_including_outside_selection(self):
      for values in ({'index': '0', 'values': '{'},
                     {'index': '-1', 'values': '{"x":"y"}'},
                     {'index': '00', 'values': '{"x":"y"}'},
                     {'index': '2', 'values': '{"x":"y"}'},
                     {'index': '0', 'values': json.dumps({'x': 1})}):
         for format in ('pcapng', 'mcap'):
            path = self.root / ('bad.' + format)
            with RecordingWriter(path, format=format, overwrite=True) as writer:
               channel = writer.register_channel(topic='ignored', catalogue=CAT, root_type='Value')
               writer._writer.add_metadata(MESSAGE_METADATA, values)
               writer.write_encoded(channel, b'\x01\x01', log_time_ns=0)
            with self.subTest(values=values, format=format), self.assertRaises(RecordingError):
               list(RecordingReader(path).iter_records(topics=['missing']))

   def test_writer_rejects_invalid_metadata_before_writing_a_message(self):
      path = self.root / 'valid.pcapng'
      with RecordingWriter(path) as writer:
         channel = writer.register_channel(topic='selected', catalogue=CAT, root_type='Value')
         with self.assertRaises(RecordingError):
            writer.write_metadata(MESSAGE_METADATA, {})
         with self.assertRaises(RecordingError):
            writer.write_encoded(channel, b'\x01\x01', metadata={'x': 1})
         writer.write_encoded(channel, b'\x01\x02', metadata={'ose.app_id': '0'})
      records = list(RecordingReader(path).iter_records())
      self.assertEqual(len(records), 1)
      self.assertEqual(records[0].metadata['ose.app_id'], '0')
