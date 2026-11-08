"""Explicit schema exports through the matching public SDL compiler CLI."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .json_source import JsonSource


SUFFIXES = {'sdl': '.sdl', 'json': '.sdl.json', 'yaml': '.sdl.yaml', 'xsd': '.sdl.xsd'}


def export_schema(project, file, format, target):
    if format not in SUFFIXES:
        raise ValueError('Choose sdl, json, yaml or xsd')
    source = project.document(file)
    destination = project.resolve(target)
    if not str(destination).endswith(SUFFIXES[format]):
        raise ValueError('Export filename must end in ' + SUFFIXES[format])
    if project.reserved(destination):
        raise ValueError('Export requires a new source file; existing files are never overwritten')
    scopes = [name for name, files in project.schema_inputs.items() if file in files]
    if not scopes:
        raise ValueError('Select a schema belonging to a registered compiler input')
    for name in project.schema_inputs:
        path = project.resolve(project.document(project.model).path.parent / name)
        if destination == path or path.is_dir() and path in destination.parents:
            raise ValueError('Export outside schema input directories to avoid duplicate declarations')
    compiler = Path(project.sdl_root) / 'sdlc/generator.py'
    base_name = re.sub(r'\.sdl(?:\.(?:json|yaml|yml|xsd))?$', '', source.path.name)
    with tempfile.TemporaryDirectory(prefix='ose-editor-export-') as temporary:
        stage = Path(temporary)
        original = project.resolve(project.document(project.model).path.parent / scopes[0])
        base = original if original.is_dir() else original.parent
        for key in project.schema_inputs[scopes[0]]:
            document = project.document(key)
            path = stage / 'input' / document.path.relative_to(base)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(document.text.encode())
        result = subprocess.run([sys.executable, str(compiler), '-' + format, str(stage / 'input'), str(stage / 'output')],
                                capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise ValueError('SDL conversion failed: ' + (result.stderr.strip().splitlines()[-1] if result.stderr.strip() else result.stdout.strip()))
        candidates = list((stage / 'output' / format).rglob(base_name + SUFFIXES[format]))
        package = getattr(source.parsed(), 'package', '')
        if len(candidates) > 1 and package:
            candidates = [path for path in candidates if path.parent.as_posix().endswith(package.replace('.', '/'))]
        if len(candidates) != 1:
            raise ValueError('Could not uniquely identify the exported source unit')
        text = candidates[0].read_text()
        if format == 'json':
            parsed = JsonSource(text)
            if 'generated_by' in parsed.root.children:
                text = parsed.delete(['generated_by'])
        else:
            lines = text.splitlines(True)
            if lines and re.search(r'(?:generated|sdlc)', lines[0], re.I) and lines[0].lstrip().startswith(('//', '#', '<!--')):
                text = ''.join(lines[1:])
    return project.load(destination, 'schema', create=True, text=text)
