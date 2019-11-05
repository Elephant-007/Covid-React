from __future__ import print_function, division, absolute_import

import itertools
from itertools import chain, product, starmap
import sys

from numba import unittest_support as unittest
from numba import jit, typeof, utils, TypingError
from .support import TestCase, MemoryLeakMixin


def slice_passing(sl):
    return sl.start, sl.stop, sl.step

def slice_constructor(*args):
    sl = slice(*args)
    return sl.start, sl.stop, sl.step

def slice_indices(s, *indargs):
    return s.indices(*indargs)


class TestSlices(MemoryLeakMixin, TestCase):

    def test_slice_passing(self):
        """
        Check passing a slice object to a Numba function.
        """
        # NOTE this also checks slice attributes
        def check(a, b, c, d, e, f):
            sl = slice(a, b, c)
            got = cfunc(sl)
            self.assertPreciseEqual(got, (d, e, f))

        maxposint = sys.maxsize
        maxnegint = -maxposint - 1
        cfunc = jit(nopython=True)(slice_passing)

        # Positive steps
        start_cases = [(None, 0), (42, 42), (-1, -1)]
        stop_cases = [(None, maxposint), (9, 9), (-11, -11)]
        step_cases = [(None, 1), (12, 12)]
        for (a, d), (b, e), (c, f) in itertools.product(start_cases,
                                                        stop_cases,
                                                        step_cases):
            check(a, b, c, d, e, f)

        # Negative steps
        start_cases = [(None, maxposint), (42, 42), (-1, -1)]
        stop_cases = [(None, maxnegint), (9, 9), (-11, -11)]
        step_cases = [(-1, -1), (-12, -12)]
        for (a, d), (b, e), (c, f) in itertools.product(start_cases,
                                                        stop_cases,
                                                        step_cases):
            check(a, b, c, d, e, f)

        # Some member is neither integer nor None
        with self.assertRaises(TypeError):
            cfunc(slice(1.5, 1, 1))

    def test_slice_constructor(self):
        """
        Test the slice() constructor in nopython mode.
        """
        maxposint = sys.maxsize
        maxnegint = -maxposint - 1
        cfunc = jit(nopython=True)(slice_constructor)
        for args, expected in [((), (0, maxposint, 1)),
                               ((None, None), (0, maxposint, 1)),
                               ((1, None), (1, maxposint, 1)),
                               ((None, 2), (0, 2, 1)),
                               ((1, 2), (1, 2, 1)),
                               ((None, None, 3), (0, maxposint, 3)),
                               ((None, 2, 3), (0, 2, 3)),
                               ((1, None, 3), (1, maxposint, 3)),
                               ((1, 2, 3), (1, 2, 3)),
                               ((None, None, -1), (maxposint, maxnegint, -1)),
                               ((10, None, -1), (10, maxnegint, -1)),
                               ((None, 5, -1), (maxposint, 5, -1)),
                               ((10, 5, -1), (10, 5, -1)),
                               ]:
            got = cfunc(*args)
            self.assertPreciseEqual(got, expected)

    def test_slice_indices(self):
        """Test that a numba slice returns same result for .indices as a python one."""
        slices = starmap(
            slice,
            product(
                chain(range(-5, 5), (None,)),
                chain(range(-5, 5), (None,)),
                chain(range(-5, 5), (None,))
            )
        )
        lengths = range(-2, 3)

        cfunc = jit(nopython=True)(slice_indices)

        for s, l in product(slices, lengths):
            if l < 0 and not utils.IS_PY3:
                # Passing a negative length to slice.indices in python2 is
                # undefined. See https://bugs.python.org/issue14794#msg174678
                continue
            try:
                expected = slice_indices(s, l)
            except Exception as py_e:
                with self.assertRaises(type(py_e)) as numba_e:
                    cfunc(s, l)
                self.assertIn(
                    str(py_e),
                    str(numba_e.exception)
                )
            else:
                self.assertPreciseEqual(expected, cfunc(s, l))

    def test_slice_indices_examples(self):
        """Tests for specific error cases."""
        cslice_indices = jit(nopython=True)(slice_indices)

        with self.assertRaises(TypingError) as e:
            cslice_indices(slice(None), 1, 2, 3)
        self.assertIn(
             "indices() takes exactly one argument (3 given)",
             str(e.exception)
        )

        with self.assertRaises(TypingError) as e:
            cslice_indices(slice(None, None, 0), 1.2)
        self.assertIn(
            "'%s' object cannot be interpreted as an integer" % typeof(1.2),
            str(e.exception)
        )


if __name__ == '__main__':
    unittest.main()
