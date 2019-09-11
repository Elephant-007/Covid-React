from __future__ import print_function, absolute_import
import os
import subprocess
import sys
import threading
import warnings
import numpy as np
from numba import jit, autojit, cuda, config
from numba.errors import (NumbaDeprecationWarning,
                          NumbaPendingDeprecationWarning, NumbaWarning)
import numba.unittest_support as unittest
from numba.targets.imputils import iternext_impl


class TestDeprecation(unittest.TestCase):

    def test_autojit(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            def dummy():
                pass
            autojit(dummy)
            self.assertEqual(len(w), 1)

    def check_warning(self, warnings, expected_str, category):
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].category, category)
        self.assertIn(expected_str, str(warnings[0].message))
        self.assertIn("http://numba.pydata.org", str(warnings[0].message))

    def test_jitfallback(self):
        # tests that @jit falling back to object mode raises a
        # NumbaDeprecationWarning
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("ignore", category=NumbaWarning)
            warnings.simplefilter("always", category=NumbaDeprecationWarning)

            def foo():
                return []  # empty list cannot be typed
            jit(foo)()

            msg = ("Fall-back from the nopython compilation path to the object "
                   "mode compilation path")
            self.check_warning(w, msg, NumbaDeprecationWarning)

    def test_reflection_of_mutable_container(self):
        # tests that reflection in list/set warns
        def foo_list(a):
            return a.append(1)

        def foo_set(a):
            return a.add(1)

        for f in [foo_list, foo_set]:
            container = f.__name__.strip('foo_')
            inp = eval(container)([10, ])
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("ignore", category=NumbaWarning)
                warnings.simplefilter("always",
                                      category=NumbaPendingDeprecationWarning)
                jit(nopython=True)(f)(inp)
                self.assertEqual(len(w), 1)
                self.assertEqual(w[0].category, NumbaPendingDeprecationWarning)
                warn_msg = str(w[0].message)
                msg = ("Encountered the use of a type that is scheduled for "
                       "deprecation")
                self.assertIn(msg, warn_msg)
                msg = ("\'reflected %s\' found for argument" % container)
                self.assertIn(msg, warn_msg)
                self.assertIn("http://numba.pydata.org", warn_msg)

    def test_iternext_impl(self):
        # tests deprecation of iternext_impl without a RefType supplied
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", category=NumbaDeprecationWarning)
            @iternext_impl
            def foo(ctx, builder, sig, args, res):
                pass
            self.assertEqual(len(w), 1)
            self.assertEqual(w[0].category, NumbaDeprecationWarning)
            warn_msg = str(w[0].message)
            msg = ("The use of iternext_impl without specifying a "
                   "numba.targets.imputils.RefType is deprecated")

    def run_cmd(self, cmdline, env, kill_is_ok=False):
        popen = subprocess.Popen(cmdline,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 env=env,
                                 shell=True)
        # finish in 20s or kill it, there's no work being done

        def kill():
            popen.stdout.flush()
            popen.stderr.flush()
            popen.kill()
        timeout = threading.Timer(20., kill)
        try:
            timeout.start()
            out, err = popen.communicate()
            retcode = popen.returncode
            if retcode != 0:
                raise AssertionError("process failed with code %s: stderr "
                                     "follows\n%s\nstdout :%s" % (retcode,
                                                                  err.decode(),
                                                                  out.decode()))
            return out.decode(), err.decode()
        finally:
            timeout.cancel()
        return None, None


if __name__ == '__main__':
    unittest.main()
