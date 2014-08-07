from __future__ import print_function

import itertools
import sys
import warnings

import numpy as np
import functools

import numba.unittest_support as unittest
from numba import types, typing, utils
from numba.compiler import compile_isolated, Flags, DEFAULT_FLAGS
from numba.numpy_support import numpy_letter_types_to_numba_types
from numba import vectorize
from numba.config import PYVERSION
from numba.typeinfer import TypingError
from numba.tests.support import TestCase, CompilationCache
from numba.targets import cpu
import re

is32bits = tuple.__itemsize__ == 4
iswindows = sys.platform.startswith('win32')

enable_pyobj_flags = Flags()
enable_pyobj_flags.set("enable_pyobject")

no_pyobj_flags = Flags()

def _unimplemented(func):
    """An 'expectedFailure' like decorator that only expects compilation errors
    caused by unimplemented functions that fail in no-python mode"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except TypingError:
            raise unittest._ExpectedFailure(sys.exc_info())
        raise unittest._UnexpectedSuccess

def _make_ufunc_usecase(ufunc):
    ldict = {}
    arg_str = ','.join(['a{0}'.format(i) for i in range(ufunc.nargs)])
    func_str = 'def fn({0}):\n    np.{1}({0})'.format(arg_str, ufunc.__name__)
    exec(func_str, globals(), ldict)
    fn = ldict['fn']
    fn.__name__ = '{0}_usecase'.format(ufunc.__name__)
    return fn


def _make_unary_ufunc_usecase(ufunc):
    ufunc_name = ufunc.__name__
    ldict = {}
    exec("def fn(x,out):\n    np.{0}(x,out)".format(ufunc_name), globals(), ldict)
    fn = ldict["fn"]
    fn.__name__ = "{0}_usecase".format(ufunc_name)
    return fn


def _make_binary_ufunc_usecase(ufunc):
    ufunc_name = ufunc.__name__
    ldict = {}
    exec("def fn(x,y,out):\n    np.{0}(x,y,out)".format(ufunc_name), globals(), ldict);
    fn = ldict['fn']
    fn.__name__ = "{0}_usecase".format(ufunc_name)
    return fn


def _as_dtype_value(tyargs, args):
    """Convert python values into numpy scalar objects.
    """
    return [np.dtype(str(ty)).type(val) for ty, val in zip(tyargs, args)]


class TestUFuncs(TestCase):

    def setUp(self):
        self.inputs = [
            (0, types.uint32),
            (1, types.uint32),
            (-1, types.int32),
            (0, types.int32),
            (1, types.int32),
            (0, types.uint64),
            (1, types.uint64),
            (-1, types.int64),
            (0, types.int64),
            (1, types.int64),

            (-0.5, types.float32),
            (0.0, types.float32),
            (0.5, types.float32),

            (-0.5, types.float64),
            (0.0, types.float64),
            (0.5, types.float64),

            (np.array([0,1], dtype='u4'), types.Array(types.uint32, 1, 'C')),
            (np.array([0,1], dtype='u8'), types.Array(types.uint64, 1, 'C')),
            (np.array([-1,0,1], dtype='i4'), types.Array(types.int32, 1, 'C')),
            (np.array([-1,0,1], dtype='i8'), types.Array(types.int64, 1, 'C')),
            (np.array([-0.5, 0.0, 0.5], dtype='f4'), types.Array(types.float32, 1, 'C')),
            (np.array([-0.5, 0.0, 0.5], dtype='f8'), types.Array(types.float64, 1, 'C')),
            ]
        self.cache = CompilationCache()

    def unary_ufunc_test(self, ufunc, flags=enable_pyobj_flags,
                         skip_inputs=[], additional_inputs=[],
                         int_output_type=None, float_output_type=None):
        ufunc = _make_unary_ufunc_usecase(ufunc)

        inputs = list(self.inputs)
        inputs.extend(additional_inputs)

        pyfunc = ufunc

        for input_tuple in inputs:
            input_operand = input_tuple[0]
            input_type = input_tuple[1]

            if input_type in skip_inputs:
                continue

            ty = input_type
            if isinstance(ty, types.Array):
                ty = ty.dtype

            if ty in types.signed_domain:
                if int_output_type:
                    output_type = types.Array(int_output_type, 1, 'C')
                else:
                    output_type = types.Array(types.int64, 1, 'C')
            elif ty in types.unsigned_domain:
                if int_output_type:
                    output_type = types.Array(int_output_type, 1, 'C')
                else:
                    output_type = types.Array(types.uint64, 1, 'C')
            else:
                if float_output_type:
                    output_type = types.Array(float_output_type, 1, 'C')
                else:
                    output_type = types.Array(types.float64, 1, 'C')

            cr = self.cache.compile(pyfunc, (input_type, output_type),
                                    flags=flags)
            cfunc = cr.entry_point

            if isinstance(input_operand, np.ndarray):
                result = np.zeros(input_operand.size,
                                  dtype=output_type.dtype.name)
                expected = np.zeros(input_operand.size,
                                    dtype=output_type.dtype.name)
            else:
                result = np.zeros(1, dtype=output_type.dtype.name)
                expected = np.zeros(1, dtype=output_type.dtype.name)

            invalid_flag = False
            with warnings.catch_warnings(record=True) as warnlist:
                warnings.simplefilter('always')

                pyfunc(input_operand, expected)

                warnmsg = "invalid value encountered"
                for thiswarn in warnlist:

                    if (issubclass(thiswarn.category, RuntimeWarning)
                        and str(thiswarn.message).startswith(warnmsg)):
                        invalid_flag = True

            cfunc(input_operand, result)

            # Need special checks if NaNs are in results
            if np.isnan(expected).any() or np.isnan(result).any():
                self.assertTrue(np.allclose(np.isnan(result), np.isnan(expected)))
                if not np.isnan(expected).all() and not np.isnan(result).all():
                    self.assertTrue(np.allclose(result[np.invert(np.isnan(result))],
                                     expected[np.invert(np.isnan(expected))]))
            else:
                match = np.all(result == expected) or np.allclose(result,
                                                                  expected)
                if not match:
                    if invalid_flag:
                        # Allow output to mismatch for invalid input
                        print("Output mismatch for invalid input",
                              input_tuple, result, expected)
                    else:
                        self.fail("%s != %s" % (result, expected))


    def binary_ufunc_test(self, ufunc, flags=enable_pyobj_flags,
                         skip_inputs=[], additional_inputs=[],
                         int_output_type=None, float_output_type=None):

        ufunc = _make_binary_ufunc_usecase(ufunc)

        inputs = list(self.inputs) + additional_inputs
        pyfunc = ufunc

        for input_tuple in inputs:
            input_operand = input_tuple[0]
            input_type = input_tuple[1]

            if input_type in skip_inputs:
                continue

            ty = input_type
            if isinstance(ty, types.Array):
                ty = ty.dtype

            if ty in types.signed_domain:
                if int_output_type:
                    output_type = types.Array(int_output_type, 1, 'C')
                else:
                    output_type = types.Array(types.int64, 1, 'C')
            elif ty in types.unsigned_domain:
                if int_output_type:
                    output_type = types.Array(int_output_type, 1, 'C')
                else:
                    output_type = types.Array(types.uint64, 1, 'C')
            else:
                if float_output_type:
                    output_type = types.Array(float_output_type, 1, 'C')
                else:
                    output_type = types.Array(types.float64, 1, 'C')

            cr = self.cache.compile(pyfunc, (input_type, input_type, output_type),
                                    flags=flags)
            cfunc = cr.entry_point

            if isinstance(input_operand, np.ndarray):
                result = np.zeros(input_operand.size,
                                  dtype=output_type.dtype.name)
                expected = np.zeros(input_operand.size,
                                    dtype=output_type.dtype.name)
            else:
                result = np.zeros(1, dtype=output_type.dtype.name)
                expected = np.zeros(1, dtype=output_type.dtype.name)
            cfunc(input_operand, input_operand, result)
            pyfunc(input_operand, input_operand, expected)

            # Need special checks if NaNs are in results
            if np.isnan(expected).any() or np.isnan(result).any():
                self.assertTrue(np.allclose(np.isnan(result), np.isnan(expected)))
                if not np.isnan(expected).all() and not np.isnan(result).all():
                    self.assertTrue(np.allclose(result[np.invert(np.isnan(result))],
                                     expected[np.invert(np.isnan(expected))]))
            else:
                self.assertTrue(np.all(result == expected) or
                                np.allclose(result, expected))


    def unary_int_ufunc_test(self, name=None, flags=enable_pyobj_flags):
        self.unary_ufunc_test(name, flags=flags,
            skip_inputs=[types.float32, types.float64,
                types.Array(types.float32, 1, 'C'),
                types.Array(types.float64, 1, 'C')])

    def binary_int_ufunc_test(self, name=None, flags=enable_pyobj_flags):
        self.binary_ufunc_test(name, flags=flags,
            skip_inputs=[types.float32, types.float64,
                types.Array(types.float32, 1, 'C'),
                types.Array(types.float64, 1, 'C')])


    ############################################################################
    # Math operations
    def test_add_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.add, flags=flags)

    def test_add_ufunc_npm(self):
        self.test_add_ufunc(flags=no_pyobj_flags)

    def test_subtract_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.subtract, flags=flags)

    def test_subtract_ufunc_npm(self):
        self.test_subtract_ufunc(flags=no_pyobj_flags)

    def test_multiply_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.multiply, flags=flags)

    def test_multiply_ufunc_npm(self):
        self.test_multiply_ufunc(flags=no_pyobj_flags)

    def test_divide_ufunc(self, flags=enable_pyobj_flags):
        # Bear in mind that in python3 divide IS true_divide
        # so the out type for int types will be a double
        int_out_type = None
        if PYVERSION >= (3, 0):
            int_out_type = types.float64

        self.binary_ufunc_test(np.divide, flags=flags, int_output_type=int_out_type)

    def test_divide_ufunc_npm(self):
        self.test_divide_ufunc(flags=no_pyobj_flags)

    def test_logaddexp_ufunc(self):
        self.binary_ufunc_test(np.logaddexp)

    def test_logaddexp_ufunc_npm(self):
        self.binary_ufunc_test(np.logaddexp, flags=no_pyobj_flags)

    def test_logaddexp2_ufunc(self):
        self.binary_ufunc_test(np.logaddexp2)

    def test_logaddexp2_ufunc_npm(self):
        self.binary_ufunc_test(np.logaddexp2, flags=no_pyobj_flags)

    def test_true_divide_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.true_divide, flags=flags, int_output_type=types.float64)

    def test_true_divide_ufunc_npm(self):
        self.test_true_divide_ufunc(flags=no_pyobj_flags)

    def test_floor_divide_ufunc(self):
        self.binary_ufunc_test(np.floor_divide)

    def test_floor_divide_ufunc_npm(self):
        self.binary_ufunc_test(np.floor_divide, flags=no_pyobj_flags)

    def test_negative_ufunc(self, flags=enable_pyobj_flags):
        # NumPy ufunc has bug with uint32 as input and int64 as output,
        # so skip uint32 input.
        self.unary_ufunc_test(np.negative, int_output_type=types.int64,
                              skip_inputs=[types.Array(types.uint32, 1, 'C'), types.uint32],
                              flags=flags)

    def test_negative_ufunc_npm(self):
        self.test_negative_ufunc(flags=no_pyobj_flags)

    def test_power_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.power, flags=flags)

    def test_power_ufunc_npm(self):
        self.test_power_ufunc(flags=no_pyobj_flags)

    def test_remainder_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.remainder, flags=flags)

    @_unimplemented
    def test_remainder_ufunc_npm(self):
        self.test_remainder_ufunc(flags=no_pyobj_flags)

    def test_mod_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.mod, flags=flags)

    @_unimplemented
    def test_mod_ufunc_npm(self):
        self.test_mod_ufunc(flags=no_pyobj_flags)

    def test_fmod_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.fmod, flags=flags)

    @_unimplemented
    def test_fmod_ufunc_npm(self):
        self.test_fmod_ufunc(flags=no_pyobj_flags)

    def test_abs_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.abs, flags=flags,
            additional_inputs = [(np.iinfo(np.uint32).max, types.uint32),
                                 (np.iinfo(np.uint64).max, types.uint64),
                                 (np.finfo(np.float32).min, types.float32),
                                 (np.finfo(np.float64).min, types.float64)
                                 ])

    def test_abs_ufunc_npm(self):
        self.test_abs_ufunc(flags=no_pyobj_flags)

    def test_absolute_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.absolute, flags=flags,
            additional_inputs = [(np.iinfo(np.uint32).max, types.uint32),
                                 (np.iinfo(np.uint64).max, types.uint64),
                                 (np.finfo(np.float32).min, types.float32),
                                 (np.finfo(np.float64).min, types.float64)
                                 ])

    def test_absolute_ufunc_npm(self):
        self.test_absolute_ufunc(flags=no_pyobj_flags)

    def test_fabs_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.fabs, flags=flags)

    def test_fabs_ufunc_npm(self):
        self.test_fabs_ufunc(flags=no_pyobj_flags)

    def test_rint_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.rint, flags=flags)

    def test_rint_ufunc_npm(self):
        self.test_rint_ufunc(flags=no_pyobj_flags)

    def test_sign_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.sign, flags=flags)

    def test_sign_ufunc_npm(self):
        self.test_sign_ufunc(flags=no_pyobj_flags)

    def test_conj_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.conj, flags=flags)

    @_unimplemented
    def test_conj_ufunc_npm(self):
        self.test_conj_ufunc(flags=no_pyobj_flags)

    def test_exp_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.exp, flags=flags)

    def test_exp_ufunc_npm(self):
        self.test_exp_ufunc(flags=no_pyobj_flags)

    def test_exp2_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.exp2, flags=flags)

    def test_exp2_ufunc_npm(self):
        self.test_exp2_ufunc(flags=no_pyobj_flags)

    def test_log_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.log, flags=flags)

    def test_log_ufunc_npm(self):
        self.test_log_ufunc(flags=no_pyobj_flags)

    def test_log2_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.log2, flags=flags)

    def test_log2_ufunc_npm(self):
        self.test_log2_ufunc(flags=no_pyobj_flags)

    def test_log10_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.log10, flags=flags)

    def test_log10_ufunc_npm(self):
        self.test_log10_ufunc(flags=no_pyobj_flags)

    def test_expm1_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.expm1, flags=flags)

    def test_expm1_ufunc_npm(self):
        self.test_expm1_ufunc(flags=no_pyobj_flags)

    def test_log1p_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.log1p, flags=flags)

    def test_log1p_ufunc_npm(self):
        self.test_log1p_ufunc(flags=no_pyobj_flags)

    def test_sqrt_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.sqrt, flags=flags)

    def test_sqrt_ufunc_npm(self):
        self.test_sqrt_ufunc(flags=no_pyobj_flags)

    def test_square_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.square, flags=flags)

    @_unimplemented
    def test_square_ufunc_npm(self):
        self.test_square_ufunc(flags=no_pyobj_flags)

    def test_reciprocal_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.reciprocal, flags=flags)

    @_unimplemented
    def test_reciprocal_ufunc_npm(self):
        self.test_reciprocal_ufunc(flags=no_pyobj_flags)

    def test_conjugate_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.conjugate, flags=flags)

    @_unimplemented
    def test_conjugate_ufunc_npm(self):
        self.test_reciprocal_ufunc(flags=no_pyobj_flags)


    ############################################################################
    # Trigonometric Functions

    def test_sin_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.sin, flags=flags)

    def test_sin_ufunc_npm(self):
        self.test_sin_ufunc(flags=no_pyobj_flags)

    def test_cos_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.cos, flags=flags)

    def test_cos_ufunc_npm(self):
        self.test_cos_ufunc(flags=no_pyobj_flags)

    def test_tan_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.tan, flags=flags)

    def test_tan_ufunc_npm(self):
        self.test_tan_ufunc(flags=no_pyobj_flags)

    def test_arcsin_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.arcsin, flags=flags)

    def test_arcsin_ufunc_npm(self):
        self.test_arcsin_ufunc(flags=no_pyobj_flags)

    def test_arccos_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.arccos, flags=flags)

    def test_arccos_ufunc_npm(self):
        self.test_arccos_ufunc(flags=no_pyobj_flags)

    def test_arctan_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.arctan, flags=flags)

    def test_arctan_ufunc_npm(self):
        self.test_arctan_ufunc(flags=no_pyobj_flags)

    def test_arctan2_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.arctan2, flags=flags)

    def test_arctan2_ufunc_npm(self):
        self.test_arctan2_ufunc(flags=no_pyobj_flags)

    def test_hypot_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.hypot)

    def test_hypot_ufunc_npm(self):
        self.test_hypot_ufunc(flags=no_pyobj_flags)

    def test_sinh_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.sinh, flags=flags)

    def test_sinh_ufunc_npm(self):
        self.test_sinh_ufunc(flags=no_pyobj_flags)

    def test_cosh_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.cosh, flags=flags)

    def test_cosh_ufunc_npm(self):
        self.test_cosh_ufunc(flags=no_pyobj_flags)

    def test_tanh_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.tanh, flags=flags)

    def test_tanh_ufunc_npm(self):
        self.test_tanh_ufunc(flags=no_pyobj_flags)

    def test_arcsinh_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.arcsinh, flags=flags)

    def test_arcsinh_ufunc_npm(self):
        self.test_arcsinh_ufunc(flags=no_pyobj_flags)

    def test_arccosh_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.arccosh, flags=flags)

    def test_arccosh_ufunc_npm(self):
        self.test_arccosh_ufunc(flags=no_pyobj_flags)

    def test_arctanh_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.arctanh, flags=flags)

    def test_arctanh_ufunc_npm(self):
        self.test_arctanh_ufunc(flags=no_pyobj_flags)

    def test_deg2rad_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.deg2rad, flags=flags)

    def test_deg2rad_ufunc_npm(self):
        self.test_deg2rad_ufunc(flags=no_pyobj_flags)

    def test_rad2deg_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.rad2deg, flags=flags)

    def test_rad2deg_ufunc_npm(self):
        self.test_rad2deg_ufunc(flags=no_pyobj_flags)

    def test_degrees_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.degrees, flags=flags)

    def test_degrees_ufunc_npm(self):
        self.test_degrees_ufunc(flags=no_pyobj_flags)

    def test_radians_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.radians, flags=flags)

    def test_radians_ufunc_npm(self):
        self.test_radians_ufunc(flags=no_pyobj_flags)


    ############################################################################
    # Bit-twiddling Functions

    def test_bitwise_and_ufunc(self, flags=enable_pyobj_flags):
        self.binary_int_ufunc_test(np.bitwise_and, flags=flags)

    @_unimplemented
    def test_bitwise_and_ufunc_npm(self):
        self.test_bitwise_and_ufunc_npm(flags=no_pyobj_flags)

    def test_bitwise_or_ufunc(self, flags=enable_pyobj_flags):
        self.binary_int_ufunc_test(np.bitwise_or, flags=flags)

    @_unimplemented
    def test_bitwise_or_ufunc_npm(self):
        self.test_bitwise_or_ufunc(flags=no_pyobj_flags)

    def test_bitwise_xor_ufunc(self, flags=enable_pyobj_flags):
        self.binary_int_ufunc_test(np.bitwise_xor, flags=flags)

    @_unimplemented
    def test_bitwise_xor_ufunc_npm(self):
        self.test_bitwise_xor_ufunc(flags=no_pyobj_flags)

    def test_invert_ufunc(self, flags=enable_pyobj_flags):
        self.unary_int_ufunc_test(np.invert, flags=flags)

    @_unimplemented
    def test_invert_ufunc_npm(self):
        self.test_invert_ufunc(flags=no_pyobj_flags)

    def test_bitwise_not_ufunc(self, flags=enable_pyobj_flags):
        self.unary_int_ufunc_test(np.bitwise_not, flags=flags)

    @_unimplemented
    def test_bitwise_not_ufunc_npm(self):
        self.test_bitwise_not_ufunc(flags=no_pyobj_flags)

    def test_left_shift_ufunc(self, flags=enable_pyobj_flags):
        self.binary_int_ufunc_test(np.left_shift, flags=flags)

    @_unimplemented
    def test_left_shift_ufunc_npm(self):
        self.test_left_shift_ufunc_npm(flags=no_pyobj_flags)

    def test_right_shift_ufunc(self, flags=enable_pyobj_flags):
        self.binary_int_ufunc_test(np.right_shift, flags=flags)

    @_unimplemented
    def test_right_shift_ufunc_npm(self):
        self.test_right_shift_ufunc(flags=no_pyobj_flags)


    ############################################################################
    # Comparison functions
    def test_greater_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.greater, flags=flags)

    @_unimplemented
    def test_greater_ufunc_npm(self):
        self.test_greater_ufunc(flags=no_pyobj_flags)

    def test_greater_equal_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.greater_equal, flags=flags)

    @_unimplemented
    def test_greater_equal_ufunc_npm(self):
        self.test_greater_equal_ufunc(flags=no_pyobj_flags)

    def test_less_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.less, flags=flags)

    @_unimplemented
    def test_less_ufunc_npm(self):
        self.test_less_ufunc(flags=no_pyobj_flags)

    def test_less_equal_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.less_equal, flags=flags)

    @_unimplemented
    def test_less_equal_ufunc_npm(self):
        self.test_less_equal_ufunc(flags=no_pyobj_flags)

    def test_not_equal_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.not_equal, flags=flags)

    @_unimplemented
    def test_not_equal_ufunc_npm(self):
        self.test_not_equal_ufunc(flags=no_pyobj_flags)

    def test_equal_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.equal, flags=flags)

    @_unimplemented
    def test_equal_ufunc_npm(self):
        self.test_equal_ufunc(flags=no_pyobj_flags)

    def test_logical_and_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.logical_and, flags=flags)

    @_unimplemented
    def test_logical_and_ufunc_npm(self):
        self.test_logical_and_ufunc(flags=no_pyobj_flags)

    def test_logical_or_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.logical_or, flags=flags)

    @_unimplemented
    def test_logical_or_ufunc_npm(self):
        self.test_logical_or_ufunc(flags=no_pyobj_flags)

    def test_logical_xor_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.logical_xor, flags=flags)

    @_unimplemented
    def test_logical_xor_ufunc_npm(self):
        self.test_logical_xor_ufunc(flags=no_pyobj_flags)

    def test_logical_not_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.logical_not, flags=flags)

    @_unimplemented
    def test_logical_not_ufunc_npm(self):
        self.test_logical_not(flags=no_pyobj_flags)

    def test_maximum_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.maximum, flags=flags)

    @_unimplemented
    def test_maximum_ufunc_npm(self):
        self.test_maximum_ufunc(flags=no_pyobj_flags)

    def test_minimum_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.minimum, flags=flags)

    @_unimplemented
    def test_minimum_ufunc_npm(self):
        self.test_minimum_ufunc(flags=no_pyobj_flags)

    def test_fmax_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.fmax, flags=flags)

    @_unimplemented
    def test_fmax_ufunc_npm(self):
        self.test_fmax_ufunc(flags=no_pyobj_flags)

    def test_fmin_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.fmin, flags=flags)

    @_unimplemented
    def test_fmin_ufunc_npm(self):
        self.test_fmin_ufunc(flags=no_pyobj_flags)


    ############################################################################
    # Floating functions
    def test_isfinite_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.isfinite, flags=flags)

    @_unimplemented
    def test_isfinite_ufunc_npm(self):
        self.test_isfinite_ufunc(flags=no_pyobj_flags)

    def test_isinf_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.isinf, flags=flags)

    @_unimplemented
    def test_isinf_ufunc_npm(self):
        self.test_isinf_ufunc(flags=no_pyobj_flags)

    def test_isnan_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.isnan, flags=flags)

    @_unimplemented
    def test_isnan_ufunc_npm(self):
        self.test_isnan_ufunc(flags=no_pyobj_flags)

    def test_signbit_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.signbit, flags=flags)

    @_unimplemented
    def test_signbit_ufunc_npm(self):
        self.test_signbit_ufunc(flags=no_pyobj_flags)

    def test_copysign_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.copysign, flags=flags)

    @_unimplemented
    def test_copysign_ufunc_npm(self):
        self.test_copysign_ufunc(flags=no_pyobj_flags)

    @_unimplemented
    def test_nextafter_ufunc(self, flags=enable_pyobj_flags):
        self.binary_ufunc_test(np.nextafter, flags=flags)

    @_unimplemented
    def test_nextafter_ufunc_npm(self):
        self.test_nextafter_ufunc(flags=no_pyobj_flags)

    def test_modf_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.modf, flags=flags)

    @_unimplemented
    def test_modf_ufunc_npm(self):
        self.test_modf_ufunc(flags=no_pyobj_flags)


    # FIXME - ldexp does not have homogeneous arguments, so the usual tests won't
    #         work as they reuse both inputs
    @unittest.skipIf(is32bits or iswindows, "Some types are not supported on "
                                       "32-bit "
                               "platform")
    @unittest.skip("this test needs fixing")
    def test_ldexp_ufunc(self, flags=enable_pyobj_flags):
        self.binary_int_ufunc_test(np.ldexp, flags=flags)

    # FIXME
    @unittest.skipIf(is32bits or iswindows,
                     "Some types are not supported on 32-bit platform")
    @unittest.skip("this tests needs fixing")
    def test_ldexp_ufunc_npm(self):
        self.test_ldexp_ufunc(flags=no_pyobj_flags)

    def test_frexp_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.frexp, flags=flags)

    @_unimplemented
    def test_frexp_ufunc_npm(self):
        self.test_frexp_ufunc(flags=no_pyobj_flags)

    def test_floor_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.floor, flags=flags)

    def test_floor_ufunc_npm(self):
        self.test_floor_ufunc(flags=no_pyobj_flags)

    def test_ceil_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.ceil, flags=flags)

    def test_ceil_ufunc_npm(self):
        self.test_ceil_ufunc(flags=no_pyobj_flags)

    def test_trunc_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.trunc, flags=flags)

    def test_trunc_ufunc_npm(self):
        self.test_trunc_ufunc(flags=no_pyobj_flags)

    def test_spacing_ufunc(self, flags=enable_pyobj_flags):
        self.unary_ufunc_test(np.spacing, flags=flags)

    @_unimplemented
    def test_spacing_ufunc_npm(self):
        self.test_spacing_ufunc(flags=nopyobj_flags)

    ############################################################################
    # Other tests
    def test_binary_ufunc_performance(self):

        pyfunc = _make_binary_ufunc_usecase(np.add)
        arraytype = types.Array(types.float32, 1, 'C')
        cr = compile_isolated(pyfunc, (arraytype, arraytype, arraytype))
        cfunc = cr.entry_point

        nelem = 5000
        x_operand = np.arange(nelem, dtype=np.float32)
        y_operand = np.arange(nelem, dtype=np.float32)
        control = np.empty_like(x_operand)
        result = np.empty_like(x_operand)

        def bm_python():
            pyfunc(x_operand, y_operand, control)

        def bm_numba():
            cfunc(x_operand, y_operand, result)

        print(utils.benchmark(bm_python, maxsec=.1))
        print(utils.benchmark(bm_numba, maxsec=.1))
        assert np.allclose(control, result)

    def binary_ufunc_mixed_types_test(self, ufunc, flags=enable_pyobj_flags):
        ufunc_name = ufunc.__name__
        ufunc = _make_binary_ufunc_usecase(ufunc)
        inputs1 = [
            (1, types.uint64),
            (-1, types.int64),
            (0.5, types.float64),

            (np.array([0, 1], dtype='u8'), types.Array(types.uint64, 1, 'C')),
            (np.array([-1, 1], dtype='i8'), types.Array(types.int64, 1, 'C')),
            (np.array([-0.5, 0.5], dtype='f8'), types.Array(types.float64, 1, 'C'))]

        inputs2 = inputs1

        output_types = [types.Array(types.int64, 1, 'C'),
                        types.Array(types.float64, 1, 'C')]

        pyfunc = ufunc

        for input1, input2, output_type in itertools.product(inputs1, inputs2, output_types):

            input1_operand = input1[0]
            input1_type = input1[1]

            input2_operand = input2[0]
            input2_type = input2[1]

            # Skip division by unsigned int because of NumPy bugs
            if ufunc_name == 'divide' and (input2_type == types.Array(types.uint32, 1, 'C') or
                    input2_type == types.Array(types.uint64, 1, 'C')):
                continue

            # Skip some subtraction tests because of NumPy bugs
            if ufunc_name == 'subtract' and input1_type == types.Array(types.uint32, 1, 'C') and \
                    input2_type == types.uint32 and types.Array(types.int64, 1, 'C'):
                continue
            if ufunc_name == 'subtract' and input1_type == types.Array(types.uint32, 1, 'C') and \
                    input2_type == types.uint64 and types.Array(types.int64, 1, 'C'):
                continue

            if ((isinstance(input1_type, types.Array) or
                    isinstance(input2_type, types.Array)) and
                    not isinstance(output_type, types.Array)):
                continue

            cr = self.cache.compile(pyfunc,
                                    (input1_type, input2_type, output_type),
                                    flags=flags)
            cfunc = cr.entry_point

            if isinstance(input1_operand, np.ndarray):
                result = np.zeros(input1_operand.size,
                                  dtype=output_type.dtype.name)
                expected = np.zeros(input1_operand.size,
                                    dtype=output_type.dtype.name)
            elif isinstance(input2_operand, np.ndarray):
                result = np.zeros(input2_operand.size,
                                  dtype=output_type.dtype.name)
                expected = np.zeros(input2_operand.size,
                                    dtype=output_type.dtype.name)
            else:
                result = np.zeros(1, dtype=output_type.dtype.name)
                expected = np.zeros(1, dtype=output_type.dtype.name)

            cfunc(input1_operand, input2_operand, result)
            pyfunc(input1_operand, input2_operand, expected)

            # Need special checks if NaNs are in results
            if np.isnan(expected).any() or np.isnan(result).any():
                self.assertTrue(np.allclose(np.isnan(result), np.isnan(expected)))
                if not np.isnan(expected).all() and not np.isnan(result).all():
                    self.assertTrue(np.allclose(result[np.invert(np.isnan(result))],
                                     expected[np.invert(np.isnan(expected))]))
            else:
                self.assertTrue(np.all(result == expected) or
                                np.allclose(result, expected))

    def test_mixed_types(self):
        self.binary_ufunc_mixed_types_test(np.divide, flags=no_pyobj_flags)


    def test_broadcasting(self):

        # Test unary ufunc
        pyfunc = _make_unary_ufunc_usecase(np.negative)

        input_operands = [
            np.arange(3, dtype='i8'),
            np.arange(3, dtype='i8').reshape(3,1),
            np.arange(3, dtype='i8').reshape(1,3),
            np.arange(3, dtype='i8').reshape(3,1),
            np.arange(3, dtype='i8').reshape(1,3),
            np.arange(3*3, dtype='i8').reshape(3,3)]

        output_operands = [
            np.zeros(3*3, dtype='i8').reshape(3,3),
            np.zeros(3*3, dtype='i8').reshape(3,3),
            np.zeros(3*3, dtype='i8').reshape(3,3),
            np.zeros(3*3*3, dtype='i8').reshape(3,3,3),
            np.zeros(3*3*3, dtype='i8').reshape(3,3,3),
            np.zeros(3*3*3, dtype='i8').reshape(3,3,3)]

        for x, result in zip(input_operands, output_operands):

            input_type = types.Array(types.uint64, x.ndim, 'C')
            output_type = types.Array(types.int64, result.ndim, 'C')

            cr = self.cache.compile(pyfunc, (input_type, output_type),
                                    flags=no_pyobj_flags)
            cfunc = cr.entry_point

            expected = np.zeros(result.shape, dtype=result.dtype)
            np.negative(x, expected)

            cfunc(x, result)
            self.assertTrue(np.all(result == expected))

        # Test binary ufunc
        pyfunc = _make_binary_ufunc_usecase(np.add)

        input1_operands = [
            np.arange(3, dtype='u8'),
            np.arange(3*3, dtype='u8').reshape(3,3),
            np.arange(3*3*3, dtype='u8').reshape(3,3,3),
            np.arange(3, dtype='u8').reshape(3,1),
            np.arange(3, dtype='u8').reshape(1,3),
            np.arange(3, dtype='u8').reshape(3,1,1),
            np.arange(3*3, dtype='u8').reshape(3,3,1),
            np.arange(3*3, dtype='u8').reshape(3,1,3),
            np.arange(3*3, dtype='u8').reshape(1,3,3)]

        input2_operands = input1_operands

        for x, y in itertools.product(input1_operands, input2_operands):

            input1_type = types.Array(types.uint64, x.ndim, 'C')
            input2_type = types.Array(types.uint64, y.ndim, 'C')
            output_type = types.Array(types.uint64, max(x.ndim, y.ndim), 'C')

            cr = self.cache.compile(pyfunc, (input1_type, input2_type, output_type),
                                    flags=no_pyobj_flags)
            cfunc = cr.entry_point

            expected = np.add(x, y)
            result = np.zeros(expected.shape, dtype='u8')

            cfunc(x, y, result)
            self.assertTrue(np.all(result == expected))


class TestScalarUFuncs(TestCase):
    """check the machinery of ufuncs works when the result is an scalar.
    These are not exhaustive because:
    - the machinery to support this case is the same for all the functions of a
      given arity.
    - the result of the inner function itself is already tested in TestUFuncs

    This class tests regular uses. A subclass tests the no python backend.
    """

    _compile_flags = enable_pyobj_flags

    def run_ufunc(self, pyfunc, arg_types, arg_values):
        for tyargs, args in zip(arg_types, arg_values):
            cr = compile_isolated(pyfunc, tyargs, flags=self._compile_flags)
            cfunc = cr.entry_point
            got = cfunc(*args)
            expected = pyfunc(*_as_dtype_value(tyargs, args))

            msg = 'for args {0} typed {1}'.format(args, tyargs)

            # note: due to semantics of ufuncs, thing like adding a int32 to a
            # uint64 results in doubles (as neither int32 can be cast safely
            # to uint64 nor vice-versa, falling back to using the float version.
            # Modify in those cases the expected value (the numpy version does
            # not use typed integers as inputs so its result is an integer)
            special = set([(types.int32, types.uint64), (types.uint64, types.int32),
                           (types.int64, types.uint64), (types.uint64, types.int64)])
            if tyargs in special:
                expected = float(expected)
            else:
                # The numba version of scalar ufuncs return an actual value that
                # gets converted to a Python type, instead of using NumPy scalars.
                # although in python 2 NumPy scalars are considered and instance of
                # the appropriate python type, in python 3 that is no longer the case.
                # This is why the expected result is casted to the appropriate Python
                # type (which is actually the expected behavior of the ufunc translation)
                if np.issubdtype(expected.dtype, np.inexact):
                    expected = float(expected)
                elif np.issubdtype(expected.dtype, np.integer):
                    expected = int(expected)
                elif np.issubdtype(expected.dtype, np.bool):
                    expected = bool(expected)

            alltypes = cr.signature.args + (cr.signature.return_type,)

            # select the appropriate precision for comparison: note that an argument
            # typed at a lower precision can introduce precision problems. For this
            # reason the argument types must be taken into account.
            if any([t==types.float32 for t in alltypes]):
                prec='single'
            elif any([t==types.float64 for t in alltypes]):
                prec='double'
            else:
                prec='exact'

            self.assertPreciseEqual(got, expected, msg=msg, prec=prec)


    def test_scalar_unary_ufunc(self):
        def _func(x):
            return np.sqrt(x)

        vals = [(2,), (2,), (1,), (2,), (.1,), (.2,)]
        tys = [(types.int32,), (types.uint32,),
               (types.int64,), (types.uint64,),
               (types.float32,), (types.float64,)]
        self.run_ufunc(_func, tys, vals)


    def test_scalar_binary_uniform_ufunc(self):
        def _func(x,y):
            return np.add(x,y)

        vals = [2, 2, 1, 2, .1, .2]
        tys = [types.int32, types.uint32,
               types.int64, types.uint64, types.float32, types.float64]
        self.run_ufunc(_func, zip(tys, tys), zip(vals, vals))


    def test_scalar_binary_mixed_ufunc(self, flags=enable_pyobj_flags):
        def _func(x,y):
            return np.add(x,y)

        vals = [2, 2, 1, 2, .1, .2]
        tys = [types.int32, types.uint32,
               types.int64, types.uint64,
               types.float32, types.float64]
        self.run_ufunc(_func, itertools.product(tys, tys),
                       itertools.product(vals, vals))


class TestScalarUFuncsNoPython(TestScalarUFuncs):
    """Same tests as TestScalarUFuncs, but forcing no python mode"""
    _compile_flags = no_pyobj_flags

class TestUfuncIssues(TestCase):
    def test_issue_651(self):
        # Exercise the code path to make sure this does not fail
        @vectorize(["(float64,float64)"])
        def foo(x1, x2):
            return np.add(x1, x2) + np.add(x1, x2)

        a = np.arange(10, dtype='f8')
        b = np.arange(10, dtype='f8')
        self.assertTrue(np.all(foo(a, b) == (a + b) + (a + b)))


class TestLoopTypes(TestCase):
    """Test code generation for the different loop types defined by ufunc.
    This test relies on class variables to configure the test. Subclasses
    of this class can just override some of these variables to check other
    ufuncs in a different compilation context. The variables supported are:

    _funcs: the ufuncs to test
    _compile_flags: compilation flags to use (to force nopython mode)
    _skip_types: letter types that force skipping the loop when testing
                 if present in the NumPy ufunc signature.
    _supported_types: only test loops where all the types in the loop
                      signature are in this collection. If unset, all.

    Note that both, _skip_types and _supported_types must be met for a loop
    to be tested.

    The NumPy ufunc signature has a form like 'ff->f' (for a binary ufunc
    loop taking 2 floats and resulting in a float). In a NumPy ufunc object
    you can get a list of supported signatures by accessing the attribute
    'types'.
    """

    _ufuncs = [np.add, np.subtract, np.multiply, np.divide, np.logaddexp,
               np.logaddexp2, np.true_divide, np.floor_divide, np.negative,
               np.power, np.remainder, np.mod, np.fmod, np.abs, np.absolute,
               np.rint, np.sign, np.conj, np.exp, np.exp2, np.log, np.log2,
               np.log10, np.expm1, np.log1p, np.sqrt, np.square, np.reciprocal,
               np.conjugate, np.sin, np.cos, np.tan, np.arcsin, np.arccos,
               np.arctan, np.arctan2, np.hypot, np.sinh, np.cosh, np.tanh,
               np.arcsinh, np.arccosh, np.arctanh, np.deg2rad, np.rad2deg,
               np.degrees, np.radians, np.bitwise_and, np.bitwise_or,
               np.bitwise_xor, np.bitwise_not, np.invert, np.left_shift,
               np.right_shift, np.greater, np.greater_equal, np.less,
               np.less_equal, np.not_equal, np.equal, np.logical_and,
               np.logical_or, np.logical_xor, np.logical_not, np.maximum,
               np.minimum, np.fmax, np.fmin, np.isfinite, np.isinf, np.isnan,
               np.signbit, np.copysign, np.nextafter, np.modf, np.ldexp,
               np.frexp, np.floor, np.ceil, np.trunc, np.spacing ]
    _compile_flags = enable_pyobj_flags
    _skip_types='O'

    def _check_loop(self, fn, ufunc, loop):
        # the letter types for the args
        letter_types = loop[:ufunc.nin] + loop[-ufunc.nout:]

        # ignore the loops containing an object argument. They will always
        # fail in no python mode. Usually the last loop in ufuncs is an all
        # object fallback
        supported_types = getattr(self, '_supported_types', [])
        skip_types = getattr(self, 'skip_types', [])
        if any(l not in supported_types or l in skip_types
               for l in letter_types):
            return

        arg_nbty = numpy_letter_types_to_numba_types(letter_types)
        arg_nbty = [types.Array(t, 1, 'C') for t in arg_nbty]
        arg_dty = [np.dtype(l) for l in letter_types]
        cr = compile_isolated(fn, arg_nbty, flags=self._compile_flags);

        # now create some really silly arguments and call the generate functions.
        # The result is checked against the result given by NumPy, but the point
        # of the test is making sure there is no compilation error.
        # 2 seems like a nice "no special case argument"

        # use days for timedelta64 and datetime64
        repl = { 'm': 'm8[d]', 'M': 'M8[D]' }
        arg_types = [repl.get(t, t) for t in letter_types]
        args1 = [np.array((2,), dtype=l) for l in arg_types]
        args2 = [np.array((2,), dtype=l) for l in arg_types]

        cr.entry_point(*args1)
        fn(*args2)

        for i in range(ufunc.nout):
            self.assertPreciseEqual(args1[-i], args2[-i])


    def _check_ufunc_loops(self, ufunc):
        fn = _make_ufunc_usecase(ufunc)
        _failed_loops = []
        for loop in ufunc.types:
            try:
                self._check_loop(fn, ufunc, loop)
            except Exception as e:
                _failed_loops.append('{2} {0}:{1}'.format(loop, str(e),
                                                          ufunc.__name__))

        return _failed_loops

    def test_ufunc_loops(self):
        failed_ufuncs = []
        failed_loops_count = 0
        for ufunc in self._ufuncs:
            failed_loops = self._check_ufunc_loops(ufunc)
            if failed_loops:
                failed_loops_count += len(failed_loops)
                msg = 'ufunc {0} failed in loops:\n\t{1}\n\t'.format(
                    ufunc.__name__,
                    '\n\t'.join(failed_loops))
                failed_ufuncs.append(msg)

        if failed_ufuncs:
            msg = 'Failed {0} ufuncs, {1} loops:\n{2}'.format(
                len(failed_ufuncs), failed_loops_count,
                '\n'.join(failed_ufuncs))

            self.fail(msg=msg)



class TestLoopTypesNoPython(TestLoopTypes):
    _compile_flags = no_pyobj_flags

    _ufuncs = [np.add, np.subtract, np.multiply, np.divide, np.logaddexp,
               np.logaddexp2, np.true_divide, np.floor_divide, np.negative,
               np.power, np.abs, np.absolute,
               np.sign, np.exp, np.exp2, np.log, np.log2,
               np.log10, np.expm1, np.log1p, np.sqrt,
               np.sin, np.cos, np.tan, np.arcsin, np.arccos,
               np.arctan, np.arctan2, np.sinh, np.cosh, np.tanh,
               np.arcsinh, np.arccosh, np.arctanh, np.deg2rad, np.rad2deg,
               np.degrees, np.radians,
               np.floor, np.ceil, np.trunc]

    # supported types are integral (signed and unsigned) as well as float and double
    # support for complex64(F) and complex128(D) should be coming soon.
    _supported_types = '?bBhHiIlLqQfd'


class TestUFuncBadArgsNoPython(TestCase):
    _compile_flags = no_pyobj_flags

    def test_missing_args(self):
        def func(x):
            """error: np.add requires two args"""
            result = np.add(x)
            return result

        self.assertRaises(TypingError, compile_isolated, func, [types.float64],
                          return_type=types.float64, flags=self._compile_flags)


    def test_too_many_args(self):
        def func(x, out, out2):
            """error: too many args"""
            result = np.add(x, x, out, out2)
            return result

        array_type = types.Array(types.float64, 1, 'C')
        self.assertRaises(TypingError, compile_isolated, func, [array_type] *3,
                          return_type=array_type, flags=self._compile_flags)

    def test_no_scalar_result_by_reference(self):
        def func(x):
            """error: scalar as a return value is not supported"""
            y = 0
            np.add(x, x, y)
        self.assertRaises(TypingError, compile_isolated, func, [types.float64],
                          return_type=types.float64, flags=self._compile_flags)




if __name__ == '__main__':
    unittest.main()
