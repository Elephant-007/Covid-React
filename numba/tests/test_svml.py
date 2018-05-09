from __future__ import division, print_function

import math
import numpy as np
import subprocess
import numbers
import importlib
import sys
import re
from itertools import chain, combinations

import numba
from numba import prange, unittest_support as unittest
from numba.compiler import compile_isolated, Flags
from numba.six import exec_
from .support import TestCase, tag, override_env_config

needs_svml = unittest.skipUnless(numba.config.USING_SVML,
                                 "SVML tests need SVML to be present")

# a map of vector lenghs with corresponding CPU architecture
vlen2cpu = {2: 'nehalem', 4: 'haswell', 8: 'skylake-avx512'}

# K: SVML functions, V: python functions which are expected to be SIMD vectorized using SVML,
# explicit references to Python functions here are mostly for sake of instant import checks.
# TODO: [] and comments below mean unused/untested SVML function, it's to be either enabled or
#       to be replaced with the explanation why the function cannot be used in Numba
# TODO: this test does not supprt functions with more than 1 arguments yet
svml_funcs = {
    "sin":     [np.sin, math.sin],
    "cos":     [np.cos, math.cos],
    "pow":        [],  # pow, math.pow],
    "exp":     [np.exp, math.exp],
    "log":     [np.log, math.log],
    "acos":    [math.acos],
    "acosh":   [math.acosh],
    "asin":    [math.asin],
    "asinh":   [math.asinh],
    "atan2":      [],  # math.atan2],
    "atan":    [math.atan],
    "atanh":   [math.atanh],
    "cbrt":       [],  # np.cbrt],
    "cdfnorm":    [],
    "cdfnorminv": [],
    "ceil":       [],  # np.ceil, math.ceil],
    "cosd":       [],
    "cosh":    [np.cosh, math.cosh],
    "erf":     [math.erf],  # np.erf is available in Intel Distribution
    "erfc":    [math.erfc],
    "erfcinv":    [],
    "erfinv":     [],
    "exp10":      [],
    "exp2":       [],  # np.exp2],
    "expm1":   [np.expm1, math.expm1],
    "floor":      [],  # np.floor, math.floor],
    "fmod":       [],  # np.fmod, math.fmod],
    "hypot":      [],  # np.hypot, math.hypot],
    "invsqrt":    [],  # available in Intel Distribution
    "log10":   [np.log10, math.log10],
    "log1p":   [np.log1p, math.log1p],
    "log2":       [],  # np.log2],
    "logb":       [],
    "nearbyint":  [],
    "rint":       [],  # np.rint],
    "round":      [],  # round],
    "sind":       [],
    "sinh":    [np.sinh, math.sinh],
    "sqrt":    [np.sqrt, math.sqrt],
    "tan":     [np.tan, math.tan],
    "tanh":    [np.tanh, math.tanh],
    "trunc":      [],  # np.trunc, math.trunc],
}

svml_funcs = {k: v for k, v in svml_funcs.items() if len(v) > 0}

# the logic should be modified if there is an SVML function being used under different name from Python
numpy_funcs = [f for f, v in svml_funcs.items() if "<ufunc" in [str(p).split(' ')[0] for p in v]]
other_funcs = [f for f, v in svml_funcs.items() if "<built-in" in [str(p).split(' ')[0] for p in v]]


def func_patterns(func, args, res, dtype, mode, vlen, flags, pad=' '*8):
    """ For a given function and modes, it returns python code with patterns it should and should not generate """

    if mode == "scalar":
        arg_list = ','.join([a+'[0]' for a in args])
        body = '%s%s[0] += math.%s(%s)\n' % (pad, res, func, arg_list)
    elif mode == "numpy":
        body = '%s%s += np.%s(%s)\n' % (pad, res, func, ','.join(args))
    else:
        assert mode == "range" or mode == "prange"
        arg_list = ','.join([a+'[i]' for a in args])
        body = '{pad}for i in {mode}({res}.size):\n{pad}{pad}{res}[i] += math.{func}({arg_list})\n'. \
               format(**locals())
    # TODO: refactor that for-loop goes into umbrella function, 'mode' can be 'numpy', '0', 'i' instead
    # TODO: that will enable mixed usecases like prange + numpy

    # type specialization
    f = func+'f' if dtype == 'float32' else func
    v = vlen*2 if dtype == 'float32' else vlen
    # general expectations
    scalar_func = '$_'+f if numba.config.IS_OSX else '$'+f
    svml_func = '__svml_%s%d%s,' % (f, v, '' if getattr(flags, 'fastmath', False) else '_ha')
    if mode == "scalar":
        contains = [scalar_func]
        avoids = [svml_func]
    else:  # will vectorize
        contains = [svml_func]
        avoids = []  # [scalar_func] - TODO: if possible, force LLVM to prevent generating the failsafe scalar paths
    # special handling
    if func == 'sqrt':
        if mode == "scalar":
            contains = ['sqrts']
            avoids = [scalar_func, svml_func]  # LLVM uses CPU instruction instead
        elif vlen == 8:
            contains = ['vsqrtp']
            avoids = [scalar_func, svml_func]  # LLVM uses CPU instruction instead
        # else expect use of SVML for older architectures
    return body, contains, avoids


def usecase_name(dtype, mode, vlen, flags):
    """ Returns pretty name for given set of modes """

    return "{dtype}_{mode}{vlen}_{flags.__name__}".format(**locals())


def combo_svml_usecase(dtype, mode, vlen, flags):
    """ Combine multiple function calls under single umbrella usecase """

    name = usecase_name(dtype, mode, vlen, flags)
    body = """def {name}(n):
        ret = np.empty(n*8, dtype=np.{dtype})
        x   = np.empty(n*8, dtype=np.{dtype})\n""".format(**locals())
    funcs = numpy_funcs if mode == "numpy" else other_funcs
    contains = []
    avoids = []
    for f in funcs:
        b, c, a = func_patterns(f, ['x'], 'ret', dtype, mode, vlen, flags)
        avoids += a
        body += b
        contains += c
    body += " "*8 + "return ret"
    ldict = {}
    exec_(body, globals(), ldict)
    ldict[name].__doc__ = body
    return ldict[name], contains, avoids


@needs_svml
class TestSVMLGeneration(TestCase):
    """ Tests all SVML-generating functions produce desired calls """

    # env mutating, must not run in parallel
    _numba_parallel_test_ = False
    asm_filter = re.compile('|'.join(['\$[a-z_]\w+,']+list(svml_funcs)))

    @classmethod
    def _inject_test(cls, dtype, mode, vlen, flags):
        args = (dtype, mode, vlen, flags)

        def test_template(self):
            fn, contains, avoids = combo_svml_usecase(*args)
            # look for specific patters in the asm for a given target
            with override_env_config('NUMBA_CPU_NAME', vlen2cpu[vlen]), \
                 override_env_config('NUMBA_CPU_FEATURES', ''):
                # recompile for overridden CPU
                jit = compile_isolated(fn, (numba.int64, ), flags=flags)
            asm = jit.library.get_asm_str()
            missed = [pattern for pattern in contains if not pattern in asm]
            found = [pattern for pattern in avoids if pattern in asm]
            self.assertTrue(not missed and not found,
                "While expecting %s and no %s,\nit contains:\n%s\nwhen compiling %s" %
                (str(missed), str(found), '\n'.join([line for line in asm.split('\n')
                    if cls.asm_filter.search(line) and not '"' in line]), fn.__doc__))
        setattr(cls, "test_"+usecase_name(*args), test_template)

    @classmethod
    def autogenerate(cls):
        test_flags = ['fastmath', ]  # TODO: add 'auto_parallel' ?
        test_flags = sum([list(combinations(test_flags, x)) for x in range(len(test_flags)+1)], [])
        flag_list = []
        for ft in test_flags:
            flags = Flags()
            flags.set('nrt')
            flags.set('error_model', 'numpy')
            flags.__name__ = '_'.join(ft+('usecase',))
            for f in ft:
                flags.set(f)
            flag_list.append(flags)
        for dtype in ('float64', 'float32'):
            for vlen in vlen2cpu:
                for flags in flag_list:
                    for mode in "scalar", "range", "prange", "numpy":
                        cls._inject_test(dtype, mode, vlen, flags)


TestSVMLGeneration.autogenerate()


def math_sin_scalar(x):
    return math.sin(x)


def math_sin_loop(n):
    ret = np.empty(n, dtype=np.float64)
    for x in range(n):
        ret[x] = math.sin(np.float64(x))
    return ret


@needs_svml
class TestSVML(TestCase):
    """ Tests SVML behaves as expected """

    # env mutating, must not run in parallel
    _numba_parallel_test_ = False

    def __init__(self, *args):
        self.flags = Flags()
        self.flags.set('nrt')

        # flags for njit(fastmath=True)
        self.fastflags = Flags()
        self.fastflags.set('nrt')
        self.fastflags.set('fastmath')
        super(TestSVML, self).__init__(*args)

    def compile(self, func, *args, **kwargs):
        assert not kwargs
        sig = tuple([numba.typeof(x) for x in args])

        std = compile_isolated(func, sig, flags=self.flags)
        fast = compile_isolated(func, sig, flags=self.fastflags)

        return std, fast

    def copy_args(self, *args):
        if not args:
            return tuple()
        new_args = []
        for x in args:
            if isinstance(x, np.ndarray):
                new_args.append(x.copy('k'))
            elif isinstance(x, np.number):
                new_args.append(x.copy())
            elif isinstance(x, numbers.Number):
                new_args.append(x)
            else:
                raise ValueError('Unsupported argument type encountered')
        return tuple(new_args)

    def check(self, pyfunc, *args, **kwargs):

        jitstd, jitfast = self.compile(pyfunc, *args)

        std_pattern = kwargs.pop('std_pattern', None)
        fast_pattern = kwargs.pop('fast_pattern', None)
        cpu_name = kwargs.pop('cpu_name', 'skylake-avx512')

        # python result
        py_expected = pyfunc(*self.copy_args(*args))

        # jit result
        jitstd_result = jitstd.entry_point(*self.copy_args(*args))

        # fastmath result
        jitfast_result = jitfast.entry_point(*self.copy_args(*args))

        # assert numerical equality
        np.testing.assert_almost_equal(jitstd_result, py_expected, **kwargs)
        np.testing.assert_almost_equal(jitfast_result, py_expected, **kwargs)

        # look for specific patters in the asm for a given target
        with override_env_config('NUMBA_CPU_NAME', cpu_name), \
             override_env_config('NUMBA_CPU_FEATURES', ''):
            # recompile for overridden CPU
            jitstd, jitfast = self.compile(pyfunc, *args)
            if std_pattern:
                self.check_svml_presence(jitstd, std_pattern)
            if fast_pattern:
                self.check_svml_presence(jitfast, fast_pattern)

    def check_svml_presence(self, func, pattern):
        asm = func.library.get_asm_str()
        self.assertIn(pattern, asm)

    def test_scalar_context(self):
        # SVML will not be used.
        pat = '$_sin' if numba.config.IS_OSX else '$sin'
        self.check(math_sin_scalar, 7., std_pattern=pat)
        self.check(math_sin_scalar, 7., fast_pattern=pat)

    @tag('important')
    def test_svml(self):
        # loops both with and without fastmath should use SVML.
        # The high accuracy routines are dropped if `fastmath` is set
        std = "__svml_sin8_ha,"
        fast = "__svml_sin8,"  # No `_ha`!
        self.check(math_sin_loop, 10, std_pattern=std, fast_pattern=fast)

    def test_svml_disabled(self):
        code = """if 1:
            import os
            import numpy as np
            import math

            def math_sin_loop(n):
                ret = np.empty(n, dtype=np.float64)
                for x in range(n):
                    ret[x] = math.sin(np.float64(x))
                return ret

            def check_no_svml():
                try:
                    # ban the use of SVML
                    os.environ['NUMBA_DISABLE_INTEL_SVML'] = '1'

                    # delay numba imports to account for env change as
                    # numba.__init__ picks up SVML and it is too late by
                    # then to override using `numba.config`
                    import numba
                    from numba import config
                    from numba.tests.support import override_env_config
                    from numba.compiler import compile_isolated, Flags

                    # compile for overridden CPU, with and without fastmath
                    with override_env_config('NUMBA_CPU_NAME', 'skylake-avx512'), \
                         override_env_config('NUMBA_CPU_FEATURES', ''):
                        sig = (numba.int32,)
                        f = Flags()
                        f.set('nrt')
                        std = compile_isolated(math_sin_loop, sig, flags=f)
                        f.set('fastmath')
                        fast = compile_isolated(math_sin_loop, sig, flags=f)
                        fns = std, fast

                        # assert no SVML call is present in the asm
                        for fn in fns:
                            asm = fn.library.get_asm_str()
                            assert '__svml_sin' not in asm
                finally:
                    # not really needed as process is separate
                    os.environ['NUMBA_DISABLE_INTEL_SVML'] = '0'
                    config.reload_config()
            check_no_svml()
            """
        popen = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = popen.communicate()
        if popen.returncode != 0:
            raise AssertionError(
                "process failed with code %s: stderr follows\n%s\n" %
                (popen.returncode, err.decode()))


if __name__ == '__main__':
    unittest.main()
