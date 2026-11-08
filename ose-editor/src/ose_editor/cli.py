"""Run the independent editor against an application source directory."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import signal
import sys
import threading


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ose-editor', description=__doc__)
    parser.add_argument('--root', default='.', help='application source directory (default: current directory)')
    parser.add_argument('--model', default='model.json')
    parser.add_argument('--deployment', action='append', default=[])
    parser.add_argument('--source-root', action='append', default=[], help='additional allowed schema/deployment root')
    parser.add_argument('--sdl-root', default=os.environ.get('SDL_ROOT', str(Path(__file__).resolve().parents[3] / 'sdl')))
    parser.add_argument('--port', type=int, default=8082)
    parser.add_argument('--create', action='store_true', help='create model/deployment drafts if missing')
    parser.add_argument('--check', action='store_true', help='inspect project and report syntax errors without starting HTTP')
    args = parser.parse_args(argv)
    from .project import Project, atomic_write
    project = None
    try:
        project = Project(args.root, args.model, args.deployment, args.source_root, args.create)
        if args.check:
            import json
            state = project.projection()
            print(json.dumps({'name': state['name'], 'documents': len(state['documents']), 'diagnostics': state['diagnostics']}))
            return int(bool(state['diagnostics']))
        from .api import create_app
        import uvicorn
        class EditorServer(uvicorn.Server):
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
        token_file = project.state / 'access.token'
        if not token_file.exists():
            atomic_write(token_file, (secrets.token_urlsafe(32) + '\n').encode())
        token = token_file.read_text().strip()
        if len(token) < 32:
            raise ValueError('Editor token must contain at least 32 characters')
        app = create_app(project, args.sdl_root, token)
        print(f'OSE Model Editor: http://127.0.0.1:{args.port}', flush=True)
        print(f'Access token file: {token_file}', flush=True)
        server = EditorServer(uvicorn.Config(app, host='127.0.0.1', port=args.port, access_log=False,
                              proxy_headers=False, limit_concurrency=128, timeout_graceful_shutdown=15))
        server.run()
        return 0 if server.started else 3
    except (OSError, ValueError) as error:
        print('Editor error: ' + str(error), file=sys.stderr)
        return 2
    finally:
        if project:
            project.close()


if __name__ == '__main__':
    raise SystemExit(main())
