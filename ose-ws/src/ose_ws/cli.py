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
from ose_ws.config import DEFAULTS, read


def initialize(path):
    path = Path(path).absolute()
    config = copy.deepcopy(DEFAULTS)
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


def serve(config):
    import uvicorn
    from ose_ws.server import create_app

    class ConsoleServer(uvicorn.Server):
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
    parser.add_argument("--config", default="ose-ws.json")
    parser.add_argument("--endpoint", help="OSE endpoint file, relative to the calling directory")
    parser.add_argument("--host", help="HTTP bind address; network access requires configured proxy authentication")
    parser.add_argument("--port", type=int, help="HTTP port (default: 8080)")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialize(args.config)
            return 0
        overrides = {}
        if args.endpoint:
            overrides["application"] = {"endpoint": str(Path(args.endpoint).resolve())}
        if args.host or args.port:
            overrides["listen"] = {key: value for key, value in (("host", args.host), ("port", args.port)) if value is not None}
        config = read(args.config, overrides)
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
