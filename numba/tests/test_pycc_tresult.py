from __future__ import print_function

import errno
import imp
import os
import shutil
import tempfile
import sys
from ctypes import *

from numba import unittest_support as unittest
from numba.pycc import find_shared_ending, find_pyext_ending, main
from numba.pycc.decorators import clear_export_registry
from .support import TestCase


base_path = os.path.dirname(os.path.abspath(__file__))


def unset_macosx_deployment_target():
    """Unset MACOSX_DEPLOYMENT_TARGET because we are not building portable
    libraries
    """
    macosx_target = os.environ.get('MACOSX_DEPLOYMENT_TARGET', None)
    if macosx_target is not None:
        del os.environ['MACOSX_DEPLOYMENT_TARGET']


class BasePYCCTest(TestCase):

    def setUp(self):
        # Note we use a permanent test directory as we can't delete
        # a DLL that's in use under Windows.
        # (this is a bit fragile if stale files can influence the result
        #  of future test runs...)
        self.tmpdir = os.path.join(tempfile.gettempdir(), "test_pycc")
        try:
            os.mkdir(self.tmpdir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise


class TestLegacyAPI(BasePYCCTest):

    def tearDown(self):
        # Since we're using the "command line" several times from the
        # same process, we must clear the exports registry between
        # invocations.
        clear_export_registry()

    def test_pycc_ctypes_lib(self):
        """
        Test creating a C shared library object using pycc.
        """
        unset_macosx_deployment_target()

        source = os.path.join(base_path, 'compile_with_pycc.py')
        cdll_modulename = 'test_dll_legacy' + find_shared_ending()
        cdll_path = os.path.join(self.tmpdir, cdll_modulename)
        if os.path.exists(cdll_path):
            os.unlink(cdll_path)

        main(args=['--debug', '-o', cdll_path, source])
        lib = CDLL(cdll_path)
        lib.mult.argtypes = [POINTER(c_double), c_void_p, c_void_p,
                             c_double, c_double]
        lib.mult.restype = c_int

        lib.multf.argtypes = [POINTER(c_float), c_void_p, c_void_p,
                              c_float, c_float]
        lib.multf.restype = c_int

        res = c_double()
        lib.mult(byref(res), None, None, 123, 321)
        self.assertEqual(res.value, 123 * 321)

        res = c_float()
        lib.multf(byref(res), None, None, 987, 321)
        self.assertEqual(res.value, 987 * 321)

    def test_pycc_pymodule(self):
        """
        Test creating a CPython extension module using pycc.
        """
        unset_macosx_deployment_target()

        source = os.path.join(base_path, 'compile_with_pycc.py')
        modulename = 'test_pyext_legacy'
        out_modulename = os.path.join(self.tmpdir,
                                      modulename + find_pyext_ending())
        if os.path.exists(out_modulename):
            os.unlink(out_modulename)

        main(args=['--debug', '--python', '-o', out_modulename, source])

        sys.path.append(self.tmpdir)
        try:
            lib = __import__(modulename)
        finally:
            sys.path.remove(self.tmpdir)
        try:
            res = lib.mult(123, 321)
            assert res == 123 * 321

            res = lib.multf(987, 321)
            assert res == 987 * 321
        finally:
            del lib

    def test_pycc_bitcode(self):
        """
        Test creating a LLVM bitcode file using pycc.
        """
        unset_macosx_deployment_target()

        modulename = os.path.join(base_path, 'compile_with_pycc')
        bitcode_modulename = os.path.join(self.tmpdir, 'test_bitcode_legacy.bc')
        if os.path.exists(bitcode_modulename):
            os.unlink(bitcode_modulename)

        main(args=['--debug', '--llvm', '-o', bitcode_modulename,
                   modulename + '.py'])

        # Sanity check bitcode file contents
        with open(bitcode_modulename, "rb") as f:
            bc = f.read()

        bitcode_wrapper_magic = b'\xde\xc0\x17\x0b'
        bitcode_magic = b'BC\xc0\xde'
        self.assertTrue(bc.startswith((bitcode_magic, bitcode_wrapper_magic)), bc)


class TestCC(BasePYCCTest):

    def setUp(self):
        super(TestCC, self).setUp()
        from . import compile_with_pycc
        self._test_module = compile_with_pycc
        imp.reload(self._test_module)

    def test_cc_properties(self):
        cc = self._test_module.cc
        self.assertEqual(cc.name, 'pycc_test_output')

        d = self._test_module.cc.output_dir
        self.assertTrue(os.path.isdir(d), d)

        f = self._test_module.cc.output_file
        self.assertFalse(os.path.exists(f), f)
        self.assertIn('pycc_test_output.', os.path.basename(f))
        if sys.platform == 'linux':
            self.assertTrue(f.endswith('.so'), f)

    def test_compile(self):
        cc = self._test_module.cc
        cc.debug = True
        cc.output_dir = self.tmpdir
        cc.compile()

        sys.path.append(self.tmpdir)
        try:
            lib = __import__(cc.name)
            res = lib.multi(123, 321)
            self.assertPreciseEqual(res, 123 * 321)
            res = lib.multf(987, 321)
            self.assertPreciseEqual(res, 987.0 * 321.0)
        finally:
            sys.path.remove(self.tmpdir)


if __name__ == "__main__":
    unittest.main()
