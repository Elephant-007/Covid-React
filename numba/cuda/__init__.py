import numba.testing
from numba.core import config

if config.ENABLE_CUDASIM:
    from .simulator_init import *
else:
    from .device_init import *
    from .device_init import _auto_device


def test(*args, **kwargs):
    if not is_available():
        raise cuda_error()

    return numba.testing.test("numba.cuda.tests", *args, **kwargs)
