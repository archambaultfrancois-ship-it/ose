"""Compile the component-only JNI byte transport for the selected JDK/platform."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
from ose.modeling.managed_containers import tool
from ose.modeling.native_containers import sdk_root


def library_name():
    return 'libose_component_transport.dylib' if sys.platform == 'darwin' else 'libose_component_transport.so'


def build(destination):
    javac = tool('JAVAC', 'javac')[0]
    home = Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else Path(shutil.which(javac) or javac).resolve().parents[1]
    if not (home / 'include/jni.h').is_file():
        raise ValueError('Java component transport requires a JDK containing include/jni.h (set JAVA_HOME)')
    platform = 'darwin' if sys.platform == 'darwin' else 'linux'
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    library = destination / library_name()
    subprocess.run(tool('CC', 'cc') + ['-std=c99', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror',
        '-I' + str(home / 'include'), '-I' + str(home / 'include' / platform),
        str(sdk_root() / 'java/ose_transport_jni.c'), '-o', str(library)], check=True)
    return library


if __name__ == '__main__':
    build(sys.argv[1])
