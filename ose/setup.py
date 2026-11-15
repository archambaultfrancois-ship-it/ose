"""Optional acceleration in wheels; SDL sources are supplied by SDL_ROOT."""
import os
from pathlib import Path
from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext


class NativeBuild(build_ext):
    def build_extension(self, ext):
        root = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parent.parent / 'sdl'))
        runtime = root / 'runtime/c'
        if not (runtime / 'type_engine.c').is_file():
            self.warn('SDL C sources unavailable; building OSE with Python kernels')
            return
        ext.include_dirs.append(str(runtime.resolve()))
        ext.sources += [str((runtime / name).resolve()) for name in (
            'type_engine.c', 'type_registry.c', 'type_codec.c', 'sdl_wire.c',
            'sdl_dynamic.c', 'type_display.c')]
        super().build_extension(ext)


setup(ext_modules=[Extension('ose._native', ['native/runtime.c'], optional=True,
                             extra_compile_args=['-O3', '-std=c11'], libraries=['m'])],
      cmdclass={'build_ext': NativeBuild})
