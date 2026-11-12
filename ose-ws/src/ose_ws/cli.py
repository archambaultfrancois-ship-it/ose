"""Initialize, validate and run an independent OSE web console."""
import argparse
from contextlib import contextmanager
import copy
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
from types import SimpleNamespace
from ose_ws.config import DEFAULTS, read


def initialize(path, *, application_root=None):
    path = Path(path).absolute()
    config = copy.deepcopy(DEFAULTS)
    if application_root is not None:
        root = Path(application_root).resolve()
        config['application'].update(root=os.path.relpath(root, path.parent),
                                     name=root.name.replace('-', ' ').replace('_', ' ').title())
    token = path.parent / config["auth"]["token_file"]
    token.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Claim configuration first. Never overwrite either an existing file or a link.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        token_fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(token_fd, "w") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
        with os.fdopen(fd, "w") as stream:
            fd = None
            json.dump(config, stream, indent=2)
            stream.write("\n")
    finally:
        if fd is not None:
            os.close(fd)
    print(f"Configuration: {path}\nAccess token file: {token}\nKeep the token private; enter it on the sign-in page.")


@contextmanager
def application_context(root):
    if root is None:
        yield None
        return
    from ose.app import Application
    context = Application(SimpleNamespace(root=root, app_sdl_root=None))
    with context.activate():
        # Runtime state and explicit paths remain relative to the caller.
        os.chdir(context.caller)
        yield context


def application_credentials(config, sdl):
    from ose.app import Application
    context = Application(SimpleNamespace(root=config['application']['root'], app_sdl_root=str(sdl)))
    context.parameter_token(SimpleNamespace(client=config['parameters']['client'],
                                           token_env=config['parameters']['token_env']))
    if 'OSE_PROCESS_PREFIX' not in os.environ:
        for path in (context.plan, context.root / 'deployment.local.json', context.root / 'deployment.json'):
            if path.is_file():
                names = context.read(path).get('process_names', {})
                if not isinstance(names, dict):
                    raise ValueError(f'invalid process names in {path}')
                prefix = names.get('prefix')
                if prefix is not None:
                    os.environ['OSE_PROCESS_PREFIX'] = prefix
                break


def serve(config):
    from ose.execution.identity import identify, initialize_thread, thread_name
    from concurrent.futures import ThreadPoolExecutor
    import asyncio
    identify("web")
    import uvicorn
    from ose_ws.server import create_app

    class ConsoleServer(uvicorn.Server):
        async def serve(self, sockets=None):
            # asyncio.to_thread workers also need native names on Python 3.10–3.12.
            label = thread_name("io")
            asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(
                thread_name_prefix=label, initializer=lambda: initialize_thread(thread_name("io"))))
            await super().serve(sockets)

        @contextmanager
        def capture_signals(self):
            if threading.current_thread() is not threading.main_thread():
                yield
                return
            previous = {sig: signal.signal(sig, self.handle_exit) for sig in (signal.SIGINT, signal.SIGTERM)}
            try:
                yield
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
            # Uvicorn normally re-raises handled signals after cleanup. The console
            # instead returns its documented normal exit code after graceful shutdown.

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = ConsoleServer(uvicorn.Config(
        create_app(config), host=config["listen"]["host"], port=config["listen"]["port"],
        workers=1, proxy_headers=False, access_log=False, ws_max_size=256 * 1024,
        ws_max_queue=16, ws_ping_interval=15, ws_ping_timeout=30, limit_concurrency=128, backlog=128,
        timeout_graceful_shutdown=config["limits"]["shutdown_s"]))
    server.run()
    return 0 if server.started else 3


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ose-ws", description=__doc__)
    parser.add_argument("command", nargs="?", choices=("init", "check", "serve"), default="serve")
    parser.add_argument("--root", help="application directory; enables application configuration and credential defaults")
    parser.add_argument("--config", help="configuration file (default: ose-ws.json in --root or the launch directory)")
    parser.add_argument("--working-directory", help="base for relative state and capture paths (default: launch directory)")
    parser.add_argument("--endpoint", help="OSE endpoint file, relative to the calling directory")
    parser.add_argument("--host", help="HTTP bind address; network access requires configured proxy authentication")
    parser.add_argument("--port", type=int, help="HTTP port (default: 8080)")
    args = parser.parse_args(argv)
    try:
        directory = Path(args.working_directory or '.').resolve()
        if not directory.is_dir():
            raise ValueError(f"Working directory does not exist: {directory}")
        overrides = {}
        if args.endpoint:
            overrides["application"] = {"endpoint": str(Path(args.endpoint).resolve())}
        if args.host or args.port:
            overrides["listen"] = {key: value for key, value in (("host", args.host), ("port", args.port)) if value is not None}
        with application_context(args.root) as context:
            path = Path(args.config).absolute() if args.config is not None else (
                context.root if context is not None else Path.cwd()) / 'ose-ws.json'
            if args.command == "init":
                initialize(path, application_root=context.root if context is not None else None)
                return 0
            if context is not None and args.command == 'serve' and args.config is None and not path.exists():
                initialize(path, application_root=context.root)
            config = read(path, overrides, working_directory=directory)
            if context is not None:
                application_credentials(config, context.sdl)
            if args.command == "check":
                from ose_ws.auth import Auth
                from ose_ws.services.connection import ApplicationConnection
                Auth(config).load()
                ApplicationConnection(config)
                print("Configuration is valid. No OSE connection or mutation was attempted.")
                return 0
            return serve(config)
    except SystemExit as error:
        return 0 if error.code == 0 else 3
    except (ValueError, KeyError, TypeError) as error:
        print(f"Invalid configuration: {error}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as error:
        print(f"Initialization failed: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
