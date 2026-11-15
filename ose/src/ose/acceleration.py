"""Optional native kernels; the Python implementation remains available."""
import os

try:
    if os.environ.get('OSE_PYTHON_NO_NATIVE'):
        raise ImportError
    from ose import _native as native
except ImportError:
    native = None
