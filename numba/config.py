from __future__ import print_function, division, absolute_import
import sys
import os

# Debug flag to control compiler debug print
DEBUG = int(os.environ.get("NUMBA_DEBUG", '0'))

DEBUG_JIT = int(os.environ.get("NUMBA_DEBUG", '0'))

# Optimization level
OPT = int(os.environ.get("NUMBA_OPT", '2'))

# Python version in (major, minor) tuple
PYVERSION = sys.version_info[:2]
