import sys
import logging

logging.basicConfig(level=logging.DEBUG)

try:
    from . import minivect
except ImportError:
    print logging.error("Did you forget to update submodule minivect?")
    print logging.error("Run 'git submodule init' followed by 'git submodule update'")
    raise

from ._numba_types import *
