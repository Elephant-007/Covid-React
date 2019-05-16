import numpy as np
from numba import njit
from numba import unittest_support as unittest
from numba.tests import support
from numba.errors import TypingError

import mymodule
import myjitmodule2 # noqa - has side-effect, overload mymodule.set_to_x


@njit
def wrap_set_to_x(arr, x):
    mymodule.set_to_x(arr, x)


class TestSetToX(support.TestCase):

    def _check(self, a, b, x):
        wrap_set_to_x(a, x)
        wrap_set_to_x.py_func(b, x)
        self.assertPreciseEqual(a, b)

    def test_int(self):
        self._check(np.zeros(10, dtype=np.int64),
                    np.zeros(10, dtype=np.int64),
                    1)

    def test_float(self):
        self._check(np.zeros(10, dtype=np.float64),
                    np.zeros(10, dtype=np.float64),
                    1.0)

    def test_float_exception_on_nan(self):
        a = np.arange(10, dtype=np.float64)
        a[0] = np.nan
        with self.assertRaises(ValueError) as e:
            wrap_set_to_x(a, 1.0)
        self.assertIn("no element of arr may be NaN",
                      str(e.exception))

    def test_type_mismatch(self):
        a = np.arange(10)
        with self.assertRaises(TypingError) as e:
            wrap_set_to_x(a, 1.0)
        self.assertIn("the types of the inputs do not match",
                      str(e.exception))

    def test_exception_on_unsupported_dtype(self):
        a = np.arange(10, dtype=np.complex128)
        with self.assertRaises(TypingError) as e:
            wrap_set_to_x(a, np.complex128(1.0))
        self.assertIn("only integer and floating-point types are allowed",
                      str(e.exception))

    def test_exception_on_tuple(self):
        a = (1, 2, 3)
        with self.assertRaises(TypingError) as e:
            wrap_set_to_x(a, 1)
        self.assertIn("tuple isn't allowed as input, use NumPy ndarray",
                      str(e.exception))


if __name__ == '__main__':
    unittest.main()
