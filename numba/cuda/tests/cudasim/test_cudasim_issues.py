import threading

import numpy as np
from numpy import float64

from numba import cuda, numba
from numba.cuda.testing import SerialMixin, skip_unless_cudasim
import numba.cuda.simulator as simulator
import unittest


class TestCudaSimIssues(SerialMixin, unittest.TestCase):
    BACKYARD_TYPE = [('statue', float64),
                     ('newspaper', float64, 6)]

    GOOSE_TYPE = [('garden', float64, (12,)),
                  ('town', float64, (42,)),
                  ('backyard', BACKYARD_TYPE)]

    GOOSE_NP_DTYPE = np.dtype(GOOSE_TYPE, align=True)

    GOOSE_NB_DTYPE = numba.from_dtype(GOOSE_NP_DTYPE)

    def test_record_access(self):
        @cuda.jit
        def simple_kernel(f):
            f.garden[0] = 45.0
            f.backyard.statue = 3.0
            f.backyard.newspaper[3] = 2.0
            f.backyard.newspaper[3] = f.backyard.newspaper[3] + 3.0

        item = np.zeros(1, self.GOOSE_NP_DTYPE)
        simple_kernel[1, 1](item[0])
        np.testing.assert_equal(45, item[0]['garden'][0])
        np.testing.assert_equal(item[0]['backyard']['statue'], 3)
        np.testing.assert_equal(item[0]['backyard']['newspaper'][3], 5)

    def test_cuda_module_in_device_function(self):
        """
        Discovered in https://github.com/numba/numba/issues/1837.
        When the `cuda` module is referenced in a device function,
        it does not have the kernel API (e.g. cuda.threadIdx, cuda.shared)
        """
        from numba.cuda.tests.cudasim import support

        inner = support.cuda_module_in_device_function

        @cuda.jit
        def outer(out):
            tid = inner()
            if tid < out.size:
                out[tid] = tid

        arr = np.zeros(10, dtype=np.int32)
        outer[1, 11](arr)
        expected = np.arange(arr.size, dtype=np.int32)
        np.testing.assert_equal(expected, arr)

    @skip_unless_cudasim('Only works on CUDASIM')
    def test_deadlock_on_exception(self):
        def assert_no_blockthreads():
            blockthreads = []
            for t in threading.enumerate():
                if not isinstance(t, simulator.kernel.BlockThread):
                    continue

                # join blockthreads with a short timeout to allow aborted threads
                # to exit
                t.join(1)
                if t.is_alive():
                    self.fail("Blocked kernel thread: %s" % t)

            self.assertListEqual(blockthreads, [])

        @simulator.jit
        def assign_with_sync(x, y):
            i = cuda.grid(1)
            y[i] = x[i]

            cuda.syncthreads()
            cuda.syncthreads()

        x = np.arange(3)
        y = np.empty(3)
        assign_with_sync[1, 3](x, y)
        np.testing.assert_array_equal(x, y)
        assert_no_blockthreads()


        with self.assertRaises(IndexError):
            assign_with_sync[1, 6](x, y)
        assert_no_blockthreads()


if __name__ == '__main__':
    unittest.main()
