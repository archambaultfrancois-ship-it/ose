"""Preserve Cargo freshness and cache generated C builds by their real inputs."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile


@contextmanager
def stable_sources(directory):
    sources = {p: (p.read_bytes(), p.stat()) for p in Path(directory).rglob('*')
               if p.is_file() and p.suffix in ('.rs', '.toml')}
    try:
        yield
    finally:
        for path, (content, stat) in sources.items():
            if path.is_file() and path.read_bytes() == content:
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def build_c(command, *, cwd, env):
    """The generated C command ends with -lm -o BINARY. Unknown dependencies rebuild."""
    binary = Path(command[-1])
    state = binary.with_name('.ose-c-build.json')
    try:
        # -M includes system and transitive headers, including user headers
        # outside the handler directory. A fixed target simplifies Make escaping.
        output = subprocess.check_output([*command[:-3], '-M', '-MT', 'ose'],
                                         cwd=cwd, env=env, stderr=subprocess.PIPE, text=True)
        paths = {Path(cwd) / name for line in output.replace('\\\n', '').splitlines()
                 for name in shlex.split(line.partition(': ')[2].replace('$$', '$'))}
        if not paths:
            raise ValueError('no compiler dependencies')
        # Track compiler/wrapper executables as well as their command and flags.
        search = os.pathsep.join(str(Path(cwd) / p) for p in env.get('PATH', os.defpath).split(os.pathsep))
        tools = [Path(found).resolve() for word in command[:command.index('-std=c99')]
                 if (found := shutil.which(str(Path(cwd) / word) if os.sep in word else word, path=search))]
        stamps = [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in tools]
        digest = hashlib.sha256(json.dumps([command, str(cwd), env, stamps], sort_keys=True).encode())
        for path in sorted(paths):
            digest.update(str(path).encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
        fingerprint = digest.hexdigest()
    except (OSError, ValueError, subprocess.CalledProcessError):
        subprocess.run(command, cwd=cwd, env=env, check=True)
        return True
    try:
        previous = json.loads(state.read_text())
        if os.access(binary, os.X_OK) and previous == dict(inputs=fingerprint, output=hashlib.sha256(binary.read_bytes()).hexdigest()):
            return False
    except (OSError, ValueError):
        pass
    subprocess.run(command, cwd=cwd, env=env, check=True)
    value = dict(inputs=fingerprint, output=hashlib.sha256(binary.read_bytes()).hexdigest())
    with tempfile.NamedTemporaryFile(mode='w', dir=state.parent, delete=False) as stream:
        json.dump(value, stream)
    os.replace(stream.name, state)
    return True
