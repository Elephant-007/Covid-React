from __future__ import print_function, absolute_import, division

import re
from collections import defaultdict, deque, namedtuple

from numba.config import MACHINE_BITS
from numba import cgutils
from llvmlite import ir, binding as llvm


_word_type = ir.IntType(MACHINE_BITS)
_pointer_type = ir.PointerType(ir.IntType(8))

_meminfo_struct_type = ir.LiteralStructType([
    _word_type,     # size_t refct
    _pointer_type,  # dtor_function dtor
    _pointer_type,  # void *dtor_info
    _pointer_type,  # void *data
    _word_type,     # size_t size
    ])


incref_decref_ty = ir.FunctionType(ir.VoidType(), [_pointer_type])
meminfo_data_ty = ir.FunctionType(_pointer_type, [_pointer_type])


def _define_nrt_meminfo_data(module):
    """
    Implement NRT_MemInfo_data in the module.  This allows inlined lookup
    of the data pointer.
    """
    fn = module.get_or_insert_function(meminfo_data_ty,
                                       name="NRT_MemInfo_data")
    builder = ir.IRBuilder(fn.append_basic_block())
    [ptr] = fn.args
    struct_ptr = builder.bitcast(ptr, _meminfo_struct_type.as_pointer())
    data_ptr = builder.load(cgutils.gep(builder, struct_ptr, 0, 3))
    builder.ret(data_ptr)


def _define_nrt_incref(module, atomic_incr):
    """
    Implement NRT_incref in the module
    """
    fn_incref = module.get_or_insert_function(incref_decref_ty,
                                              name="NRT_incref")
    builder = ir.IRBuilder(fn_incref.append_basic_block())
    [ptr] = fn_incref.args
    is_null = builder.icmp_unsigned("==", ptr, cgutils.get_null_value(ptr.type))
    with cgutils.if_unlikely(builder, is_null):
        builder.ret_void()
    builder.call(atomic_incr, [builder.bitcast(ptr, atomic_incr.args[0].type)])
    builder.ret_void()


def _define_nrt_decref(module, atomic_decr):
    """
    Implement NRT_decref in the module
    """
    fn_decref = module.get_or_insert_function(incref_decref_ty,
                                              name="NRT_decref")
    calldtor = module.add_function(ir.FunctionType(ir.VoidType(), [_pointer_type]),
                                   name="NRT_MemInfo_call_dtor")

    builder = ir.IRBuilder(fn_decref.append_basic_block())
    [ptr] = fn_decref.args
    is_null = builder.icmp_unsigned("==", ptr, cgutils.get_null_value(ptr.type))
    with cgutils.if_unlikely(builder, is_null):
        builder.ret_void()
    newrefct = builder.call(atomic_decr,
                            [builder.bitcast(ptr, atomic_decr.args[0].type)])

    refct_eq_0 = builder.icmp_unsigned("==", newrefct,
                                       ir.Constant(newrefct.type, 0))
    with cgutils.if_unlikely(builder, refct_eq_0):
        builder.call(calldtor, [ptr])
    builder.ret_void()


# Set this to True to measure the overhead of atomic refcounts compared
# to non-atomic.
_disable_atomicity = 0


def _define_atomic_inc_dec(module, op, ordering):
    """Define a llvm function for atomic increment/decrement to the given module
    Argument ``op`` is the operation "add"/"sub".  Argument ``ordering`` is
    the memory ordering.  The generated function returns the new value.
    """
    ftype = ir.FunctionType(_word_type, [_word_type.as_pointer()])
    fn_atomic = ir.Function(module, ftype, name="nrt_atomic_{0}".format(op))

    [ptr] = fn_atomic.args
    bb = fn_atomic.append_basic_block()
    builder = ir.IRBuilder(bb)
    ONE = ir.Constant(_word_type, 1)
    if not _disable_atomicity:
        oldval = builder.atomic_rmw(op, ptr, ONE, ordering=ordering)
        # Perform the operation on the old value so that we can pretend returning
        # the "new" value.
        res = getattr(builder, op)(oldval, ONE)
        builder.ret(res)
    else:
        oldval = builder.load(ptr)
        newval = getattr(builder, op)(oldval, ONE)
        builder.store(newval, ptr)
        builder.ret(oldval)

    return fn_atomic


def _define_atomic_cmpxchg(module, ordering):
    """Define a llvm function for atomic compare-and-swap.
    The generated function is a direct wrapper of the LLVM cmpxchg with the
    difference that the a int indicate success (1) or failure (0) is returned
    and the last argument is a output pointer for storing the old value.

    Note
    ----
    On failure, the generated function behaves like an atomic load.  The loaded
    value is stored to the last argument.
    """
    ftype = ir.FunctionType(ir.IntType(32), [_word_type.as_pointer(),
                                             _word_type, _word_type,
                                             _word_type.as_pointer()])
    fn_cas = ir.Function(module, ftype, name="nrt_atomic_cas")

    [ptr, cmp, repl, oldptr] = fn_cas.args
    bb = fn_cas.append_basic_block()
    builder = ir.IRBuilder(bb)
    outtup = builder.cmpxchg(ptr, cmp, repl, ordering=ordering)
    old, ok = cgutils.unpack_tuple(builder, outtup, 2)
    builder.store(old, oldptr)
    builder.ret(builder.zext(ok, ftype.return_type))

    return fn_cas


def _define_atomic_ops(module):
    _define_atomic_inc_dec(module, "add", ordering='monotonic')
    _define_atomic_inc_dec(module, "sub", ordering='monotonic')
    _define_atomic_cmpxchg(module, ordering='monotonic')
    return module


def compile_nrt_functions(ctx):
    """
    Compile all LLVM NRT functions and return a library containing them.
    The library is created using the given target context.
    """
    codegen = ctx.jit_codegen()
    library = codegen.create_library("nrt")

    # Implement LLVM module with atomic ops
    ir_mod = library.create_ir_module("nrt_module")

    atomic_inc = _define_atomic_inc_dec(ir_mod, "add", ordering='monotonic')
    atomic_dec = _define_atomic_inc_dec(ir_mod, "sub", ordering='monotonic')
    _define_atomic_cmpxchg(ir_mod, ordering='monotonic')

    _define_nrt_meminfo_data(ir_mod)
    _define_nrt_incref(ir_mod, atomic_inc)
    _define_nrt_decref(ir_mod, atomic_dec)

    library.add_ir_module(ir_mod)
    library.finalize()

    return library


_regex_incref = re.compile(r'call void @NRT_incref\((.*)\)')
_regex_decref = re.compile(r'call void @NRT_decref\((.*)\)')
_regex_bb = re.compile(r'[-a-zA-Z$._][-a-zA-Z$._0-9]*:')


def remove_redundant_nrt_refct(ll_module):
    """
    Remove redundant reference count operations from the
    `llvmlite.binding.ModuleRef`. This parses the ll_module as a string and
    line by line to remove the unnecessary nrt refct pairs within each block.

    Note
    -----
    Should replace this.  Not efficient.
    """
    # Early escape if NRT_incref is not used
    try:
        ll_module.get_function('NRT_incref')
    except NameError:
        return ll_module


    incref_map = defaultdict(deque)
    decref_map = defaultdict(deque)
    scopes = []

    # Parse IR module as text
    llasm = str(ll_module)
    lines = llasm.splitlines()

    # Phase 1:
    # Find all refct ops and what they are operating on
    for lineno, line in enumerate(lines):
        # Match NRT_incref calls
        m = _regex_incref.match(line.strip())
        if m is not None:
            incref_map[m.group(1)].append(lineno)
            continue

        # Match NRT_decref calls
        m = _regex_decref.match(line.strip())
        if m is not None:
            decref_map[m.group(1)].append(lineno)
            continue

        # Split at BB boundaries
        m = _regex_bb.match(line)
        if m is not None:
            # Push
            scopes.append((incref_map, decref_map))
            # Reset
            incref_map = defaultdict(deque)
            decref_map = defaultdict(deque)


    # Phase 2:
    # Determine which refct ops are unnecessary
    to_remove = set()
    for incref_map, decref_map in scopes:
        # For each value being refct-ed
        for val in incref_map.keys():
            increfs = incref_map[val]
            decrefs = decref_map[val]
            # Mark the incref/decref pairs from the tail for removal
            ref_pair_ct = min(len(increfs), len(decrefs))
            for _ in range(ref_pair_ct):
                to_remove.add(increfs.pop())
                to_remove.add(decrefs.popleft())

    # Phase 3
    # Remove all marked instructions
    newll = '\n'.join(ln for lno, ln in enumerate(lines) if lno not in
                      to_remove)

    # Regenerate the LLVM module
    return llvm.parse_assembly(newll)
