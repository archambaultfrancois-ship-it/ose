"""SDL2 component graph execution, independent of the SDL codec repository."""
import sys

if sys.version_info < (3, 10):
    raise RuntimeError('Python 3.10+ is required. Use a Python 3.10+ interpreter from PATH.')
