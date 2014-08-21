"""Codegen for functions used as kernels in NumPy functions

Typically, the kernels of several ufuncs that can't map directly to
Python builtins
"""

from __future__ import print_function, absolute_import, division


from .. import cgutils, typing, types
from llvm import core as lc


def _check_arity_and_homogeneous(sig, args, arity):
    """checks that the following are true:
    - args and sig.args have arg_count elements
    - all types are homogeneous
    """
    assert len(args) == 2
    assert len(sig.args) == 2
    ty = sig.args[0]
    # must have homogeneous args
    assert all(arg==ty for arg in sig.args) and sig.return_type == ty


def _call_func_by_name_with_cast(context, builder, sig, args,
                                 func_name):
    # it is quite common in NumPy to have loops implemented as a call
    # to the double version of the function, wrapped in casts. This
    # helper function facilitates that.
    mod = cgutils.get_module(builder)
    double = lc.Type.double()
    fnty = lc.Type.function(double, [double, double])
    fn = mod.get_or_insert_function(fnty, name=func_name)
    cast_args = [context.cast(builder, arg, argty, type_)
             for arg, argty in zip(args, sig.args) ]

    result = builder.call(fn, cast_args)
    return context.cast(builder, result, types.float64, sig.result_type)


def _dispatch_func_by_name_type(context, builder, sig, args, table, user_name):
    # assumes types in the sig are homogeneous.
    # assumes that the function pointed by func_name has the type
    # signature sig (but needs translation to llvm types).
    try:
        func_name = table[sig.result_type] # any would do... homogeneous
    except KeyError as e:
        raise LoweringError("No {0} function for real type {1}".format(user_name, str(e)))

    mod = cgutils.get_module(builder)
    argtypes = [context.get_argument_type(aty) for aty in sig.args]
    restype = context.get_argument_type(sig.result_type)
    fnty = lc.Type.function(restype, argtypes)
    fn = mod.get_or_insert_function(fnty, name=func_name)
    return builder.call(fn, args)


########################################################################
# Division kernels inspired by NumPy loops.c.src code
#
# The builtins are not applicable as they rely on a test for zero in the
# denominator. If it is zero the appropriate exception is raised.
# In NumPy, a division by zero does not raise an exception, but instead
# generated a known value. Note that a division by zero in any of the
# operations of a vector may raise an exception or issue a warning
# depending on the numpy.seterr configuration. This is not supported
# right now (and in any case, it won't be handled by these functions
# either)

def np_int_sdiv_impl(context, builder, sig, args):
    # based on the actual code in NumPy loops.c.src for signed integer types
    num, den = args
    lltype = num.type
    assert all(i.type==lltype for i in args), "must have homogeneous types"

    ZERO = lc.Constant.int(lltype, 0)
    MINUS_ONE = lc.Constant.int(lltype, -1)
    MIN_INT = lc.Constant.int(lltype, 1 << (den.type.width-1))
    den_is_zero = builder.icmp(lc.ICMP_EQ, ZERO, den)
    den_is_minus_one = builder.icmp(lc.ICMP_EQ, MINUS_ONE, den)
    num_is_min_int = builder.icmp(lc.ICMP_EQ, MIN_INT, num)
    could_cause_sigfpe = builder.and_(den_is_minus_one, num_is_min_int)
    force_zero = builder.or_(den_is_zero, could_cause_sigfpe)
    with cgutils.ifelse(builder, force_zero, expect=False) as (then, otherwise):
        with then:
            bb_then = builder.basic_block
        with otherwise:
            bb_otherwise = builder.basic_block
            div = builder.sdiv(num, den)
            mod = builder.srem(num, den)
            num_gt_zero = builder.icmp(lc.ICMP_SGT, num, ZERO)
            den_gt_zero = builder.icmp(lc.ICMP_SGT, den, ZERO)
            not_same_sign = builder.xor(num_gt_zero, den_gt_zero)
            mod_not_zero = builder.icmp(lc.ICMP_NE, mod, ZERO)
            needs_fixing = builder.and_(not_same_sign, mod_not_zero)
            fix_value = builder.select(needs_fixing, MINUS_ONE, ZERO)
            result_otherwise = builder.add(div, fix_value)
    result = builder.phi(lltype)
    result.add_incoming(ZERO, bb_then)
    result.add_incoming(result_otherwise, bb_otherwise)

    return result


def np_int_udiv_impl(context, builder, sig, args):
    num, den = args
    lltype = num.type
    assert all(i.type==lltype for i in args), "must have homogeneous types"

    ZERO = lc.Constant.int(lltype, 0)
    div_by_zero = builder.icmp(lc.ICMP_EQ, ZERO, den)
    with cgutils.ifelse(builder, div_by_zero, expect=False) as (then, otherwise):
        with then:
            # division by zero
            bb_then = builder.basic_block
        with otherwise:
            # divide!
            div = builder.udiv(num, den)
            bb_otherwise = builder.basic_block
    result = builder.phi(lltype)
    result.add_incoming(ZERO, bb_then)
    result.add_incoming(div, bb_otherwise)
    return result


def np_real_div_impl(context, builder, sig, args):
    # in NumPy real div has the same semantics as an fdiv for generating
    # NANs, INF and NINF
    num, den = args
    lltype = num.type
    assert all(i.type==lltype for i in args), "must have homogeneous types"
    return builder.fdiv(*args)


def _fabs(context, builder, arg):
    ZERO = lc.Constant.real(arg.type, 0.0)
    arg_negated = builder.fsub(ZERO, arg)
    arg_is_negative = builder.fcmp(lc.FCMP_OLT, arg, ZERO)
    return builder.select(arg_is_negative, arg_negated, arg)


def np_complex_div_impl(context, builder, sig, args):
    # Extracted from numpy/core/src/umath/loops.c.src,
    # inspired by complex_div_impl
    # variables named coherent with loops.c.src
    # This is implemented using the approach described in
    #   R.L. Smith. Algorithm 116: Complex division.
    #   Communications of the ACM, 5(8):435, 1962

    complexClass = context.make_complex(sig.args[0])
    in1, in2 = [complexClass(context, builder, value=arg) for arg in args]

    in1r = in1.real  # numerator.real
    in1i = in1.imag  # numerator.imag
    in2r = in2.real  # denominator.real
    in2i = in2.imag  # denominator.imag
    ftype = in1r.type
    assert all([i.type==ftype for i in [in1r, in1i, in2r, in2i]]), "mismatched types"
    out = complexClass(context, builder)

    ZERO = lc.Constant.real(ftype, 0.0)
    ONE = lc.Constant.real(ftype, 1.0)

    # if abs(denominator.real) >= abs(denominator.imag)
    in2r_abs = _fabs(context, builder, in2r)
    in2i_abs = _fabs(context, builder, in2i)
    in2r_abs_ge_in2i_abs = builder.fcmp(lc.FCMP_OGE, in2r_abs, in2i_abs)
    with cgutils.ifelse(builder, in2r_abs_ge_in2i_abs) as (then, otherwise):
        with then:
            # if abs(denominator.real) == 0 and abs(denominator.imag) == 0
            in2r_is_zero = builder.fcmp(lc.FCMP_OEQ, in2r_abs, ZERO)
            in2i_is_zero = builder.fcmp(lc.FCMP_OEQ, in2i_abs, ZERO)
            in2_is_zero = builder.and_(in2r_is_zero, in2i_is_zero)
            with cgutils.ifelse(builder, in2_is_zero) as (inn_then, inn_otherwise):
                with inn_then:
                    # division by 0.
                    # fdiv generates the appropriate NAN/INF/NINF
                    out.real = builder.fdiv(in1r, in2r_abs)
                    out.imag = builder.fdiv(in1i, in2i_abs)
                with inn_otherwise:
                    # general case for:
                    # abs(denominator.real) > abs(denominator.imag)
                    rat = builder.fdiv(in2i, in2r)
                    # scl = 1.0/(in2r + in2i*rat)
                    tmp1 = builder.fmul(in2i, rat)
                    tmp2 = builder.fadd(in2r, tmp1)
                    scl = builder.fdiv(ONE, tmp2)
                    # out.real = (in1r + in1i*rat)*scl
                    # out.imag = (in1i - in1r*rat)*scl
                    tmp3 = builder.fmul(in1i, rat)
                    tmp4 = builder.fmul(in1r, rat)
                    tmp5 = builder.fadd(in1r, tmp3)
                    tmp6 = builder.fsub(in1i, tmp4)
                    out.real = builder.fmul(tmp5, scl)
                    out.imag = builder.fmul(tmp6, scl)
        with otherwise:
            # general case for:
            # abs(denominator.imag) > abs(denominator.real)
            rat = builder.fdiv(in2r, in2i)
            # scl = 1.0/(in2i + in2r*rat)
            tmp1 = builder.fmul(in2r, rat)
            tmp2 = builder.fadd(in2i, tmp1)
            scl = builder.fdiv(ONE, tmp2)
            # out.real = (in1r*rat + in1i)*scl
            # out.imag = (in1i*rat - in1r)*scl
            tmp3 = builder.fmul(in1r, rat)
            tmp4 = builder.fmul(in1i, rat)
            tmp5 = builder.fadd(tmp3, in1i)
            tmp6 = builder.fsub(tmp4, in1r)
            out.real = builder.fmul(tmp5, scl)
            out.imag = builder.fmul(tmp6, scl)

    return out._getvalue()


########################################################################
# true div kernels

def np_int_truediv_impl(context, builder, sig, args):
    # in NumPy we don't check for 0 denominator... fdiv handles div by
    # 0 in the way NumPy expects..
    # integer truediv always yields double
    num, den = args
    lltype = num.type
    assert all(i.type==lltype for i in args), "must have homogeneous types"
    numty, denty = sig.args

    num = context.cast(builder, num, numty, types.float64)
    den = context.cast(builder, den, denty, types.float64)

    return builder.fdiv(num,den)


########################################################################
# floor div kernels

def np_real_floor_div_impl(context, builder, sig, args):
    res = np_real_div_impl(context, builder, sig, args)
    s = typing.signature(sig.return_type, sig.return_type)
    return np_real_floor_impl(context, builder, s, (res,))


def np_complex_floor_div_impl(context, builder, sig, args):
    # this is based on the complex floor divide in Numpy's loops.c.src
    # This is basically a full complex division with a complex floor
    # applied.
    # The complex floor seems to be defined as the real floor applied
    # with the real part and zero in the imaginary part. Fully developed
    # so it avoids computing anything related to the imaginary result.
    float_kind = sig.args[0].underlying_float
    floor_sig = typing.signature(float_kind, float_kind)

    complexClass = context.make_complex(sig.args[0])
    in1, in2 = [complexClass(context, builder, value=arg) for arg in args]

    in1r = in1.real
    in1i = in1.imag
    in2r = in2.real
    in2i = in2.imag
    ftype = in1r.type
    assert all([i.type==ftype for i in [in1r, in1i, in2r, in2i]]), "mismatched types"

    ZERO = lc.Constant.real(ftype, 0.0)

    out = complexClass(context, builder)
    out.imag = ZERO

    in2r_abs = _fabs(context, builder, in2r)
    in2i_abs = _fabs(context, builder, in2i)
    in2r_abs_ge_in2i_abs = builder.fcmp(lc.FCMP_OGE, in2r_abs, in2i_abs)

    with cgutils.ifelse(builder, in2r_abs_ge_in2i_abs) as (then, otherwise):
        with then:
            rat = builder.fdiv(in2i, in2r)
            # out.real = floor((in1r+in1i*rat)/(in2r + in2i*rat))
            tmp1 = builder.fmul(in1i, rat)
            tmp2 = builder.fmul(in2i, rat)
            tmp3 = builder.fadd(in1r, tmp1)
            tmp4 = builder.fadd(in2r, tmp2)
            tmp5 = builder.fdiv(tmp3, tmp4)
            out.real = np_real_floor_impl(context, builder, floor_sig, (tmp5,))
        with otherwise:
            rat = builder.fdiv(in2r, in2i)
            # out.real = floor((in1i + in1r*rat)/(in2i + in2r*rat))
            tmp1 = builder.fmul(in1r, rat)
            tmp2 = builder.fmul(in2r, rat)
            tmp3 = builder.fadd(in1i, tmp1)
            tmp4 = builder.fadd(in2i, tmp2)
            tmp5 = builder.fdiv(tmp3, tmp4)
            out.real = np_real_floor_impl(context, builder, floor_sig, (tmp5,))
    return out._getvalue()


########################################################################
# numpy power funcs

def np_int_power_impl(context, builder, sig, args):
    # In NumPy ufunc loops, integer power is performed using the double
    # version of power with the appropriate casts
    assert len(args) == 2
    assert len(sig.args) == 2
    ty = sig.args[0]
    # must have homogeneous args
    assert all(arg==ty for arg in sig.args) and sig.return_type == ty

    return _call_func_by_name_with_cast(context, builder, sig, args,
                                       'numba.npymath.power')


def np_real_power_impl(context, builder, sig, args):
    _check_arity_and_homogeneous(sig, args, 2)

    dispatch_table = {
        types.float32: 'numba.npymath.powf',
        types.float64: 'numba.npymath.pow',
    }

    return _dispatch_func_by_name_type(context, builder, sig, args,
                                       dispatch_table, 'power')


def np_complex_power_impl(context, builder, sig, args):
    _check_arity_and_homogeneous(sig, args, 2)

    dispatch_table = {
        types.complex64: 'numba.npymath.cpowf',
        types.complex128: 'numba.npymath.cpow',
    }

    return _dispatch_func_by_name_type(context, builder, sig, args,
                                       dispatch_table, 'power')


def np_real_floor_impl(context, builder, sig, args):
    assert len(args) == 1
    assert len(sig.args) == 1
    ty = sig.args[0]
    assert ty == sig.return_type, "must have homogeneous types"
    mod = cgutils.get_module(builder)
    if ty == types.float64:
        fnty = lc.Type.function(lc.Type.double(), [lc.Type.double()])
        fn = mod.get_or_insert_function(fnty, name="numba.npymath.floor")
    elif ty == types.float32:
        fnty = lc.Type.function(lc.Type.float(), [lc.Type.float()])
        fn = mod.get_or_insert_function(fnty, name="numba.npymath.floorf")
    else:
        raise LoweringError("No floor function for real type {0}".format(str(ty)))

    return builder.call(fn, args)
