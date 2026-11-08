"""Local project HTTP API with revision checks and browser-origin protection."""
from contextlib import asynccontextmanager
import hmac
import json
from pathlib import Path
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .project import Conflict
from .validation import Validator


def create_app(project, sdl_root, token=None):
    token = token or secrets.token_urlsafe(32)
    project.sdl_root = Path(sdl_root).resolve()
    validator = Validator(project, sdl_root)

    @asynccontextmanager
    async def lifespan(app):
        yield
        validator.close()
        project.close()

    app = FastAPI(title='OSE Model Editor', version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.token = token
    app.state.project = project
    app.state.validator = validator

    @app.middleware('http')
    async def protect(request: Request, call_next):
        if request.url.path.startswith('/api/'):
            host = request.headers.get('host', '')
            origin = request.headers.get('origin')
            if origin and origin not in (f'http://{host}', f'https://{host}'):
                return JSONResponse({'message': 'Cross-origin access is not allowed'}, status_code=403)
            supplied = request.headers.get('authorization', '').removeprefix('Bearer ')
            if not hmac.compare_digest(supplied, token):
                return JSONResponse({'message': 'Enter the editor access token'}, status_code=401)
            length = request.headers.get('content-length')
            if length and int(length) > 8 * 1024 * 1024:
                return JSONResponse({'message': 'Request exceeds the 8 MiB limit'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.exception_handler(Conflict)
    async def conflict(request, error):
        return JSONResponse({'message': str(error), 'conflicts': getattr(error, 'conflicts', [])}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({'message': str(error)}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({'message': 'Object no longer exists: ' + str(error)}, status_code=404)

    @app.get('/api/v1/project')
    def read_project():
        with project.mutex:
            return project.projection()

    @app.get('/api/v1/capabilities')
    def capabilities():
        import importlib.metadata
        try:
            engine = importlib.metadata.version('ose')
        except importlib.metadata.PackageNotFoundError:
            engine = 'checkout-v1'
        coverage = Path(__file__).with_name('coverage.json')
        return {'editor': __version__, 'ose': engine, 'model_version': 1, 'sdl': 'SDL2/source-v1', 'sdl_root': str(validator.sdl_root),
                'coverage': json.loads(coverage.read_text()) if coverage.exists() else [],
                'formats': ['json', 'sdl', 'yaml', 'xsd'], 'validation': True, 'business_code_execution': False}

    @app.get('/api/v1/document')
    def document(file: str):
        with project.mutex:
            doc = project.document(file)
            return {'file': file, 'text': doc.text, 'revision': project.revision, 'format': doc.format}

    @app.get('/api/v1/changes')
    def changes():
        with project.mutex:
            return {'revision': project.revision, 'changes': project.differences()}

    @app.post('/api/v1/commands/prepare')
    def prepare(body: dict):
        return project.prepare(body['command'], body['revision'])

    @app.post('/api/v1/commands/apply')
    def apply(body: dict):
        return project.apply(body['id'], body['revision'])

    @app.post('/api/v1/undo')
    def undo(body: dict):
        return project.undo(body['revision'])

    @app.post('/api/v1/redo')
    def redo(body: dict):
        return project.undo(body['revision'], redo=True)

    @app.post('/api/v1/save')
    def save(body: dict):
        return project.save(body['revision'], body.get('allow_invalid', False))

    @app.post('/api/v1/recovery')
    def recover(body: dict):
        return project.recover(body['choice'])

    @app.post('/api/v1/draft-recovery')
    def recover_draft(body: dict):
        return project.recover_draft(body['revision'], body.get('restore', False))

    @app.post('/api/v1/validation')
    def start_validation(body: dict):
        return validator.start(body['revision'], body.get('deployment'), body.get('schema'))

    @app.get('/api/v1/validation/{identifier}')
    def validation(identifier: str):
        result = validator.snapshot(identifier)
        result['stale'] = result['revision'] != project.revision
        return result

    @app.delete('/api/v1/validation/{identifier}')
    def cancel_validation(identifier: str):
        return validator.cancel(identifier)

    @app.get('/api/v1/usages')
    def usages(name: str):
        result = []
        with project.mutex:
            for key, doc in project.documents.items():
                if not doc.active:
                    continue
                # Source occurrences are explicitly labelled; parsed rename
                # commands use semantic references rather than this search.
                for number, line in enumerate(doc.text.splitlines(), 1):
                    if name in line:
                        result.append({'file': key, 'line': number, 'text': line.strip()[:240], 'kind': 'source occurrence'})
        return {'items': result[:1000], 'external_consumers': 'Not assessed'}

    static = Path(__file__).with_name('static')
    if static.is_dir():
        app.mount('/', StaticFiles(directory=static, html=True), name='frontend')
    return app
