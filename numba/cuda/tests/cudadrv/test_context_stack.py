from __future__ import print_function

import numbers
from ctypes import byref
import weakref

from numba import cuda
from numba.cuda.testing import unittest, SerialMixin
from numba.cuda.cudadrv import driver


class TestContextStack(SerialMixin, unittest.TestCase):
    def setUp(self):
        # Reset before testing
        cuda.close()

    def test_gpus_current(self):
        self.assertIs(cuda.gpus.current, None)
        with cuda.gpus[0]:
            self.assertEqual(cuda.gpus.current.id, 0)

    def test_gpus_len(self):
        self.assertGreater(len(cuda.gpus), 0)

    def test_gpus_iter(self):
        gpulist = list(cuda.gpus)
        self.assertGreater(len(gpulist), 0)


class TestContextAPI(SerialMixin, unittest.TestCase):

    def tearDown(self):
        cuda.close()

    def test_context_memory(self):
        mem = cuda.current_context().get_memory_info()

        self.assertIsInstance(mem.free, numbers.Number)
        self.assertEquals(mem.free, mem[0])

        self.assertIsInstance(mem.total, numbers.Number)
        self.assertEquals(mem.total, mem[1])

        self.assertLessEqual(mem.free, mem.total)

    @unittest.skipIf(len(cuda.gpus) < 2, "need more than 1 gpus")
    def test_forbidden_context_switch(self):
        # Cannot switch context inside a `cuda.require_context`
        @cuda.require_context
        def switch_gpu():
            with cuda.gpus[1]:
                pass

        with cuda.gpus[0]:
            with self.assertRaises(RuntimeError) as raises:
                switch_gpu()

            self.assertIn("Cannot switch CUDA-context.", str(raises.exception))

    @unittest.skipIf(len(cuda.gpus) < 2, "need more than 1 gpus")
    def test_accepted_context_switch(self):
        def switch_gpu():
            with cuda.gpus[1]:
                return cuda.current_context().device.id

        with cuda.gpus[0]:
            devid = switch_gpu()
        self.assertEqual(devid, 1)


class Test3rdPartyContext(SerialMixin, unittest.TestCase):
    def tearDown(self):
        cuda.close()

    def test_attached_primary(self):
        # Emulate primary context creation by 3rd party
        the_driver = driver.driver
        hctx = driver.drvapi.cu_context()
        the_driver.cuDevicePrimaryCtxRetain(byref(hctx), 0)
        try:
            ctx = driver.Context(weakref.proxy(self), hctx)
            ctx.push()
            # Check that the context from numba matches the created primary
            # context.
            my_ctx = cuda.current_context()
            self.assertEqual(my_ctx.handle.value, ctx.handle.value)
        finally:
            ctx.pop()
            the_driver.cuDevicePrimaryCtxRelease(0)

    def test_attached_non_primary(self):
        # Emulate non-primary context creation by 3rd party
        the_driver = driver.driver
        hctx = driver.drvapi.cu_context()
        the_driver.cuCtxCreate(byref(hctx), 0, 0)
        try:
            cuda.current_context()
        except RuntimeError as e:
            # Expecting an error about non-primary CUDA context
            self.assertIn("Numba cannot operate on non-primary CUDA context ",
                          str(e))
        else:
            self.fail("No RuntimeError raised")
        finally:
            the_driver.cuCtxDestroy(hctx)


if __name__ == '__main__':
    unittest.main()
