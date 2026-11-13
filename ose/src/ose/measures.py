"""Read current application measurements through the existing status RPC."""
import asyncio
from datetime import datetime, timezone
from fnmatch import fnmatchcase
import json
import math
import ssl
import sys

from ose.execution.measurements import validate_snapshot
from ose.top import safe_text
from ose.transport.endpoint import resolve
from ose.transport.secure import connect, finish_cleanup


def sample(snapshot, patterns=('*',)):
    if snapshot.get('measurements_version') != 1:
        raise ValueError('measurements require an updated OSE runtime')
    inventory = snapshot.get('observation', {}).get('components', {})
    values = {}
    for component, desc in sorted(snapshot.get('components', {}).items()):
        if not inventory.get(component, {}).get('connected', False):
            continue
        metrics = desc.get('metrics', desc)
        selected = {name: value for name, value in sorted(validate_snapshot(metrics.get('measurements', {})).items())
                    if any(fnmatchcase(f'{component}.{name}', pattern) or fnmatchcase(name, pattern)
                           for pattern in patterns)}
        if selected:
            values[component] = selected
    return {'timestamp': datetime.now(timezone.utc).isoformat(), 'epoch': snapshot.get('epoch'),
            'measurements': values}


def render(report):
    lines = [report['timestamp']]
    if report.get('error'):
        lines.append('UNAVAILABLE: ' + safe_text(report['error']))
    else:
        rows = [(safe_text(component), name, '-' if value is None else repr(value))
                for component, values in report['measurements'].items() for name, value in values.items()]
        widths = [max([len(row[i]) for row in rows] + [len(title)])
                  for i, title in enumerate(('COMPONENT', 'MEASUREMENT', 'VALUE'))]
        for row in [('COMPONENT', 'MEASUREMENT', 'VALUE'), *rows]:
            lines.append('  '.join(value.ljust(width) for value, width in zip(row, widths)).rstrip())
    return '\n'.join(lines)


async def run(args, output=None):
    output = output or sys.stdout
    if not math.isfinite(args.interval) or args.interval < .1:
        raise ValueError('interval must be finite and at least 0.1 seconds')
    if args.count is not None and args.count < 1:
        raise ValueError('count must be positive')
    if args.endpoint and (args.host is not None or args.port is not None):
        raise ValueError('choose --endpoint or --host/--port')
    resolve(args)
    count = args.count if args.count is not None else (None if args.watch else 1)
    client, collected = None, 0
    try:
        while True:
            try:
                if client is None:
                    endpoint = resolve(args)
                    client = await connect(endpoint.host, endpoint.port, 1024 * 1024, endpoint.context,
                                           'coordinator' if endpoint.distributed else None, timeout=3)
                snapshot, _ = await client.request('status', timeout=3)
                report = sample(snapshot, args.patterns or ('*',))
            except ssl.SSLError:
                raise
            except (OSError, EOFError, ConnectionError, asyncio.TimeoutError) as error:
                report = {'timestamp': datetime.now(timezone.utc).isoformat(), 'measurements': {},
                          'error': str(error) or type(error).__name__}
                if client is not None:
                    await client.close()
                    client = None
            print(json.dumps(report, allow_nan=False) if args.format == 'jsonl' else render(report),
                  file=output, flush=True)
            collected += 1
            if count is not None and collected >= count:
                return 1 if report.get('error') else 0
            await asyncio.sleep(args.interval)
    finally:
        if client is not None:
            await finish_cleanup(client.close())
