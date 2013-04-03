# -*- coding: utf-8 -*-

"""
>>> tuple_unpacking_error(2)
Traceback (most recent call last):
    ...
NumbaError: ...: Only NumPy attributes and list or tuple literals can currently be unpacked
"""

from __future__ import print_function, division, absolute_import

import numba
from numba import *
import numpy as np

@autojit
def tuple_unpacking_error(obj):
    a, b = obj

if __name__ == "__main__":
    numba.testmod()
