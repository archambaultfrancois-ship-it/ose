"""Read and follow the runtime's bounded in-memory component log stream."""
import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from ose.execution.logs import LEVELS
from ose.top import safe_text
from ose.transport.secure import connect, contexts, finish_cleanup


def add_parser(commands):
    command = commands.add_parser('logs', help='read or follow component logs retained in runtime memory')
    command.add_argument('components', nargs='*', help='component names or globs (default: all)')
    command.add_argument('--level', action='append', choices=list(LEVELS.values()), help='include this level; repeat to select several')
    command.add_argument('--search', default='', help='case-insensitive message text')
    command.add_argument('--logger', default='*', help='category name or glob')
    command.add_argument('--limit', type=int, default=200, help='recent records per request (1 to 200)')
    command.add_argument('--follow', '-f', action='store_true', help='follow new records until interrupted')
    command.add_argument('--format', choices=('text', 'jsonl'), default='text')
    command.add_argument('--interval', type=float, default=.5, help='follow interval in seconds (minimum 0.1)')
    command.add_argument('--endpoint', help='endpoint JSON (default: build/endpoint.json)')
    command.add_argument('--host', help='broker or coordinator host')
    command.add_argument('--port', type=int, help='broker or coordinator port')
    command.add_argument('--distributed', action='store_true')
    command.add_argument('--insecure', action='store_true', help='distributed loopback development mode')
    for name in ('tls-ca', 'tls-cert', 'tls-key'):
        command.add_argument('--' + name, help='distributed mutual TLS certificate/key')


def render(record):
    stamp = datetime.fromtimestamp(int(record['time_ns']) / 1e9, timezone.utc).isoformat(timespec='milliseconds')
    category = f" [{safe_text(record['logger'])}]" if record['logger'] else ''
    return f"{stamp} {record['level']:<8} {safe_text(record['component'])}{category}: {safe_text(record['message'])}"


async def run(args, output=None):
    output = output or sys.stdout
    if not math.isfinite(args.interval) or args.interval < .1 or not 1 <= args.limit <= 200:
        raise ValueError('logs require interval >= 0.1 and limit between 1 and 200')
    if args.endpoint and (args.host is not None or args.port is not None):
        raise ValueError('choose --endpoint or --host/--port')
    distributed = args.distributed or args.insecure or any((args.tls_ca, args.tls_cert, args.tls_key))
    def address():
        path = args.endpoint or ('build/endpoint.json' if args.host is None and args.port is None and not distributed else None)
        value = json.loads(Path(path).read_text()) if path else {
            'host': args.host if args.host is not None else '127.0.0.1',
            'port': args.port if args.port is not None else 7000}
        if not isinstance(value.get('host'), str) or not value['host'] or type(value.get('port')) is not int or not 1 <= value['port'] <= 65535:
            raise ValueError('invalid log service endpoint')
        return value['host'], value['port']
    host, port = address()
    context = contexts(args.tls_ca, args.tls_cert, args.tls_key, insecure=args.insecure, hosts=[host])[1] if distributed else None
    filters = dict(components=args.components or ['*'], levels=args.level, search=args.search, logger=args.logger, limit=args.limit)
    client = None
    try:
        while True:
            try:
                if client is None:
                    host, port = address()
                    if distributed and args.insecure:
                        contexts(insecure=True, hosts=[host])
                    client = await connect(host, port, 1024 * 1024, context, 'coordinator' if distributed else None, timeout=3)
                result, _ = await client.request('logs', filters=filters, timeout=3)
                if result['truncated']:
                    print('Log history was truncated or the runtime restarted; some entries are unavailable.', file=sys.stderr)
                for record in result['items']:
                    print(json.dumps(record, ensure_ascii=False) if args.format == 'jsonl' else render(record), file=output, flush=True)
                filters.update(after=result['cursor'], epoch=result['epoch'])
                if not args.follow:
                    return 0
                if not result['more']:
                    await asyncio.sleep(args.interval)
            except BrokenPipeError:
                return 0
            except (OSError, EOFError, ConnectionError, asyncio.TimeoutError) as error:
                if not args.follow:
                    raise
                print('Log stream unavailable: ' + safe_text(str(error)), file=sys.stderr)
                if client is not None:
                    await client.close()
                    client = None
                await asyncio.sleep(args.interval)
    finally:
        if client is not None:
            await finish_cleanup(client.close())
