"""CLI for complete parameter blocks; all access goes through the service."""
from ose.execution.plan import read_plan
import argparse
import asyncio
import json
import math
import os
import re
from pathlib import Path
from ose.modeling.model import unique_pairs

from ose.parameters.files import (MAX_JSON_SIZE, MAX_TOTAL_JSON_SIZE, read_source, read_value, validate_import)


def parser(commands):
    command = commands.add_parser('parameter', help='read, write, watch or synchronize shared SDL parameter blocks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Access complete SDL parameter blocks through the running parameter service.\n'
                    'Paths use slash-separated names, such as motor/control. Each action requires\n'
                    'a declared --client identity; its token is read from OSE_PARAMETER_TOKEN by default.',
        epilog='Examples:\n'
               '  ose parameter list --client operator\n'
               '  ose parameter get motor/control --client operator\n'
               '  ose parameter sync --dir parameters --client operator\n\n'
               'Use ose parameter ACTION --help for options and examples.')
    actions = command.add_subparsers(dest='parameter_action', required=True, title='actions', metavar='ACTION')
    descriptions = {
        'list': ('list parameter blocks visible to a client',
                 'Print the paths of parameter blocks accessible to the client as a JSON array.',
                 ['ose parameter list --client operator']),
        'get': ('read a block or export matching blocks as JSON',
                'With PATH, read one literal block and print its value.\n'
                'With --export, print {path: block} and interpret PATH as a Python regular\n'
                'expression searched in each path; --exact selects a literal path instead.\n'
                'Omitting PATH exports all blocks visible to the client.',
                ['ose parameter get motor/control --client operator',
                 "ose parameter get '^motor/' --export --client operator > parameters.json",
                 'ose parameter get --client operator > parameters.json']),
        'set': ('replace a block or import a JSON export',
                'With PATH and --file, replace one complete block using its JSON value.\n'
                'Without PATH, import a {path: block} JSON export, or recursively import\n'
                '*.json exports using --dir. Use --file - to read standard input.\n'
                'Imports validate all blocks before writing; writes are applied per block.',
                ['ose parameter set motor/control --file control.json --client operator',
                 'ose parameter set --file parameters.json --client operator',
                 'ose parameter set --dir parameters --client operator']),
        'watch': ('stream changes to one parameter block',
                  'Watch one literal block path and print JSON updates until interrupted.\n'
                  'Subscriptions keep the latest value when updates arrive faster than reads.',
                  ['ose parameter watch motor/control --client operator',
                   'ose parameter watch motor/control --count 5 --client operator']),
        'sync': ('synchronize parameter blocks with a directory of JSON files',
                 'Synchronize selected blocks with a local directory until interrupted.\n'
                 'Each PATH maps to DIR/PATH.json containing {path: block}. PATH is a Python\n'
                 'regular expression searched in each path; omit it for all blocks or use --exact.\n'
                 'Initially, existing files update the application by default; missing files are\n'
                 'created from application values. Use --initial app to overwrite files instead.\n'
                 'Subsequent changes propagate in both directions. Requires the OSE sync extra.',
                 ['ose parameter sync --dir parameters --client operator',
                  "ose parameter sync '^motor/' --dir parameters --initial app --client operator"]),
    }
    for name in ('list', 'get', 'set', 'watch', 'sync'):
        summary, description, examples = descriptions[name]
        action = actions.add_parser(name, help=summary, description=description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog='Examples:\n' + '\n'.join('  ' + example for example in examples))
        if name in ('get', 'set', 'sync'):
            path_help = {'get': 'literal block path, or regex with --export (default: export all)',
                         'set': 'literal block path for a single value file; omit for exports or --dir',
                         'sync': 'path regex, or literal path with --exact (default: all accessible blocks)'}
            action.add_argument('path', nargs='?', help=path_help[name])
        elif name == 'watch':
            action.add_argument('path', help='literal slash-separated parameter block path')
        if name == 'get':
            action.add_argument('--exact', action='store_true', help='read one literal path instead of matching a pattern')
            action.add_argument('--export', action='store_true', help='export {path: block}; PATH is a regex by default, omitted selects all')
        if name == 'set':
            source = action.add_mutually_exclusive_group(required=True)
            source.add_argument('--file', help='block JSON with PATH, or {path: block} export without PATH; - for stdin')
            source.add_argument('--dir', help='recursively import all *.json exports in a directory; no PATH')
        if name == 'watch':
            action.add_argument('--count', type=int, help='exit after N updates (default: unlimited)')
        if name == 'sync':
            action.add_argument('--dir', required=True, help='tree of unit {path: block} JSON files')
            action.add_argument('--exact', action='store_true', help='select one literal path')
            action.add_argument('--initial', choices=('files', 'app'), default='files',
                                help='initial source of values: existing files or application (default: files)')
            action.add_argument('--debounce', type=float, default=.15, help='file event grouping seconds (default: 0.15)')
        if name in ('set', 'sync'):
            action.add_argument('--max-json-size', type=int, default=MAX_JSON_SIZE,
                                help='maximum bytes per JSON input (default: 16777216 / 16 MiB)')
        if name == 'set':
            action.add_argument('--max-total-json-size', type=int, default=MAX_TOTAL_JSON_SIZE,
                                help='maximum total JSON bytes for --dir (default: 67108864 / 64 MiB)')
        action.add_argument('--client', required=True, help='client identity declared in the parameter configuration')
        action.add_argument('--token-env', default='OSE_PARAMETER_TOKEN', help='environment variable containing the client token (default: OSE_PARAMETER_TOKEN)')
        action.add_argument('--endpoint', default='build/endpoint.json', help='active application endpoint JSON (default: build/endpoint.json)')
        action.add_argument('--host', help='service or coordinator host; with --host, also supply --port')
        action.add_argument('--port', type=int, help='service or coordinator TCP port (default: from endpoint)')
        action.add_argument('--plan', default='build/plan.json', help='plan used to locate the SDL runtime (default: build/plan.json)')
        action.add_argument('--sdl-root', default=os.environ.get('SDL_ROOT'), help='SDL checkout (default: SDL_ROOT, plan runtime or installed runtime)')
        action.add_argument('--distributed', action='store_true', help='connect through a distributed coordinator; inferred from endpoint metadata by default')
        action.add_argument('--tls-ca', help='trusted CA certificate file for distributed mutual TLS')
        action.add_argument('--tls-cert', help='distributed client certificate file; common name must match --client')
        action.add_argument('--tls-key', help='private key file matching --tls-cert')
        action.add_argument('--insecure', action='store_true', help='explicit distributed loopback-only development mode')


def read_directory(directory, max_size=MAX_JSON_SIZE, max_total=MAX_TOTAL_JSON_SIZE):
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f'parameter directory does not exist or is not a directory: {root}')
    def walk_error(error):
        raise error
    files = []
    for folder, _, names in os.walk(root, onerror=walk_error, followlinks=False):
        files.extend(Path(folder) / name for name in names
                     if name.endswith('.json') and (Path(folder) / name).is_file())
    if not files:
        raise ValueError(f'no *.json files found in parameter directory: {root}')
    merged, origins, total = {}, {}, 0
    for source in sorted(files, key=lambda path: path.relative_to(root).as_posix()):
        try:
            data = read_source(source, max_size)
            total += len(data)
            if total > max_total:
                raise ValueError(f'parameter directory JSON exceeds {max_total} bytes in total')
            value = json.loads(data.decode('utf-8'), object_pairs_hook=unique_pairs)
            validate_import(value)
        except (OSError, ValueError) as error:
            raise ValueError(f'{source}: {error}') from error
        for path, block in value.items():
            if path in origins:
                raise ValueError(f'duplicate parameter path {path}: {origins[path]} and {source}')
            merged[path], origins[path] = block, source
        validate_import(merged)
    return merged


def connection_factory(options):
    """Resolve the endpoint on each connection, including after restart."""
    from ose.parameters.client import ParameterClient
    from ose.transport.endpoint import resolve
    from ose.codec.runtime import Runtime
    if options.sdl_root:
        from ose.codec.sdl2 import SDL2
        runtime = SDL2(options.sdl_root)
    elif Path(options.plan).is_file():
        from ose.codec.sdl2 import SDL2
        runtime = SDL2(read_plan(options.plan)['sdl_root'])
    else:
        try:
            runtime = Runtime()
        except ModuleNotFoundError as error:
            if error.name != 'sdl_runtime':
                raise
            raise ValueError('select --sdl-root, a compiled --plan, or install the SDL Python runtime') from error
    async def connect():
        endpoint = resolve(options)
        return await ParameterClient.connect(endpoint.host, endpoint.port, options.client, token=os.environ.get(options.token_env),
            context=endpoint.context, distributed=endpoint.distributed, runtime=runtime)
    return connect


async def run(options):
    value = None
    if options.parameter_action in ('set', 'sync') and options.max_json_size <= 0:
        raise ValueError('--max-json-size must be positive')
    if options.parameter_action == 'set':
        if options.max_total_json_size <= 0:
            raise ValueError('--max-total-json-size must be positive')
        if options.dir is not None:
            if options.path is not None:
                raise ValueError('--dir cannot be combined with a positional parameter path')
            value = read_directory(options.dir, options.max_json_size, options.max_total_json_size)
        else:
            value = read_value(options.file, options.max_json_size)
    pattern = None
    if options.parameter_action in ('get', 'sync'):
        if options.exact and options.path is None:
            raise ValueError(f'{options.parameter_action} --exact requires PATH')
        # Keep existing value-only CLI reads literal. Application commands select
        # export mode, where a positional pattern (or all paths) is the default.
        if not options.exact and (options.parameter_action == 'sync' or options.export or options.path is None):
            try:
                pattern = re.compile(options.path if options.path is not None else '.*')
            except re.error as error:
                raise ValueError(f'invalid parameter path regex: {error}') from error
    if options.parameter_action == 'set' and options.path is None:
        validate_import(value)
    if options.parameter_action == 'watch' and options.count is not None and options.count <= 0:
        raise ValueError('watch count must be positive')
    connect = connection_factory(options)
    if options.parameter_action == 'sync':
        from ose.execution.identity import identify
        identify('sync')
        if not math.isfinite(options.debounce) or options.debounce < 0:
            raise ValueError('--debounce must be finite and nonnegative')
        from ose.parameters.sync import DirectorySync
        await DirectorySync(options.dir, connect, pattern=pattern, exact=options.path if options.exact else None,
                            initial=options.initial, debounce=options.debounce, max_json_size=options.max_json_size).run()
        return
    client = await connect()
    async with client:
        if options.parameter_action == 'list':
            print(json.dumps(await client.list()), flush=True)
            return
        if options.parameter_action == 'get':
            paths = [options.path] if pattern is None else [path for path in await client.list() if pattern.search(path)]
            exported = {}
            for path in paths:
                block = await client.block(path)
                exported[path] = await block.get()
            result = exported if options.export or pattern is not None else exported[options.path]
            print(json.dumps(result, allow_nan=False), flush=True)
        elif options.parameter_action == 'set':
            if options.path is not None:
                block = await client.block(options.path)
                await block.set(value)
                print(json.dumps({'updated': options.path}), flush=True)
            else:
                # Validate every block before the first write. Service-side
                # failures may still occur after earlier writes have succeeded.
                blocks = {}
                for path, contents in value.items():
                    blocks[path] = await client.block(path)
                    blocks[path].values.encode(contents)
                updated = []
                for path, block in blocks.items():
                    try:
                        await block.set(value[path])
                    except (ValueError, OSError, asyncio.TimeoutError) as error:
                        raise ValueError(f'parameter import failed at {path}; updated blocks: '
                                         f'{json.dumps(updated)}; {error}') from error
                    updated.append(path)
                print(json.dumps({'updated': updated}), flush=True)
        else:
            await client.subscribe([options.path])
            count = 0
            while options.count is None or count < options.count:
                update = await client.next()
                if update is not None:
                    path, value = update
                    print(json.dumps({'path': path, 'value': value}, allow_nan=False), flush=True)
                    count += 1
