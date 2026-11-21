"""Generic CH/2 output collector, recording actual callback receptions as JSONL."""
import json
from pathlib import Path

from ose.codec.sdl2 import SDL2
from ose.execution.input_process import InputSession
from ose.parameters.values import Values


def main():
    with InputSession(auto_ready=False) as session:
        settings = session.config['parameters']
        codec = SDL2(settings['sdl_root'])
        desc = session.config['inputs']['input']
        values = Values(codec.api, desc['catalogue'], desc['root'])
        count, size = 0, 0
        with Path(settings['path']).open('x', encoding='utf-8') as stream:
            session.ready()
            for reference in session.activations():
                record = reference.record
                if record is None:
                    raise ValueError('collector requires a message activation')
                envelope = record['body']
                codec.validate_only(desc['catalogue'], desc['root'], envelope)
                payload = codec.api.unpack_envelope(envelope)[1]
                item = dict(output=settings['output'], time_ns=reference.time_ns,
                            value=values.decode(payload))
                if 'app_id' in record:
                    item['app_id'] = record['app_id']
                line = json.dumps(item, ensure_ascii=True, allow_nan=False) + '\n'
                count += 1
                size += len(line.encode())
                if count > settings['max_messages'] or size > settings['max_bytes']:
                    raise ValueError('collector message/byte budget exceeded; collection is incomplete')
                stream.write(line)
                stream.flush()


if __name__ == '__main__':
    main()
