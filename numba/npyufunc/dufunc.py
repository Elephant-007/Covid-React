from __future__ import absolute_import, print_function, division

from numba import serialize

from .. import jit, typeof, utils, types, numpy_support, sigutils
from ..typing import npydecl
from ..typing.templates import AbstractTemplate, signature
from . import _internal, ufuncbuilder
from ..dispatcher import Dispatcher
from .. import array_analysis


def make_dufunc_kernel(_dufunc):
    from ..targets import npyimpl

    class DUFuncKernel(npyimpl._Kernel):
        """
        npyimpl._Kernel subclass responsible for lowering a DUFunc kernel
        (element-wise function) inside a broadcast loop (which is
        generated by npyimpl.numpy_ufunc_kernel()).
        """
        dufunc = _dufunc

        def __init__(self, context, builder, outer_sig):
            super(DUFuncKernel, self).__init__(context, builder, outer_sig)
            self.inner_sig, self.cres = self.dufunc.find_ewise_function(
                outer_sig.args)

        def generate(self, *args):
            isig = self.inner_sig
            osig = self.outer_sig
            cast_args = [self.cast(val, inty, outty)
                         for val, inty, outty in
                         zip(args, osig.args, isig.args)]
            if self.cres.objectmode:
                func_type = self.context.call_conv.get_function_type(
                    types.pyobject, [types.pyobject] * len(isig.args))
            else:
                func_type = self.context.call_conv.get_function_type(
                    isig.return_type, isig.args)
            module = self.builder.block.function.module
            entry_point = module.get_or_insert_function(
                func_type, name=self.cres.fndesc.llvm_func_name)
            entry_point.attributes.add("alwaysinline")

            _, res = self.context.call_conv.call_function(
                self.builder, entry_point, isig.return_type, isig.args,
                cast_args)
            return self.cast(res, isig.return_type, osig.return_type)

    DUFuncKernel.__name__ += _dufunc.ufunc.__name__
    return DUFuncKernel


class DUFuncLowerer(object):
    '''Callable class responsible for lowering calls to a specific DUFunc.
    '''
    def __init__(self, dufunc):
        self.kernel = make_dufunc_kernel(dufunc)
        self.libs = []

    def __call__(self, context, builder, sig, args):
        from ..targets import npyimpl
        explicit_output = len(args) > self.kernel.dufunc.ufunc.nin
        return npyimpl.numpy_ufunc_kernel(context, builder, sig, args,
                                          self.kernel,
                                          explicit_output=explicit_output)


class DUFunc(_internal._DUFunc):
    """
    Dynamic universal function (DUFunc) intended to act like a normal
    Numpy ufunc, but capable of call-time (just-in-time) compilation
    of fast loops specialized to inputs.
    """
    # NOTE: __base_kwargs must be kept in synch with the kwlist in
    # _internal.c:dufunc_init()
    __base_kwargs = set(('identity', '_keepalive', 'nin', 'nout'))

    def __init__(self, py_func, identity=None, cache=False, targetoptions={}):
        if isinstance(py_func, Dispatcher):
            py_func = py_func.py_func
        dispatcher = jit(target='npyufunc',
                         cache=cache,
                         **targetoptions)(py_func)
        self._initialize(dispatcher, identity)

    def _initialize(self, dispatcher, identity):
        identity = ufuncbuilder.parse_identity(identity)
        super(DUFunc, self).__init__(dispatcher, identity=identity)
        # Loop over a copy of the keys instead of the keys themselves,
        # since we're changing the dictionary while looping.
        self._install_type()
        self._lower_me = DUFuncLowerer(self)
        self._install_cg()
        self.__name__ = dispatcher.py_func.__name__
        self.__doc__ = dispatcher.py_func.__doc__

    def __reduce__(self):
        siglist = list(self._dispatcher.overloads.keys())
        return (serialize._rebuild_reduction,
                (self.__class__, self._dispatcher, self.identity,
                 self._frozen, siglist))

    @classmethod
    def _rebuild(cls, dispatcher, identity, frozen, siglist):
        self = _internal._DUFunc.__new__(cls)
        self._initialize(dispatcher, identity)
        # Re-add signatures
        for sig in siglist:
            self.add(sig)
        if frozen:
            self.disable_compile()
        return self

    def build_ufunc(self):
        """
        For compatibility with the various *UFuncBuilder classes.
        """
        return self

    @property
    def targetoptions(self):
        return self._dispatcher.targetoptions

    @property
    def nin(self):
        return self.ufunc.nin

    @property
    def nout(self):
        return self.ufunc.nout

    @property
    def nargs(self):
        return self.ufunc.nargs

    @property
    def ntypes(self):
        return self.ufunc.ntypes

    @property
    def types(self):
        return self.ufunc.types

    @property
    def identity(self):
        return self.ufunc.identity

    def disable_compile(self):
        """
        Disable the compilation of new signatures at call time.
        """
        # If disabling compilation then there must be at least one signature
        assert len(self._dispatcher.overloads) > 0
        self._frozen = True

    def add(self, sig):
        """
        Compile the DUFunc for the given signature.
        """
        args, return_type = sigutils.normalize_signature(sig)
        return self._compile_for_argtys(args, return_type)

    def _compile_for_args(self, *args, **kws):
        nin = self.ufunc.nin
        if kws:
            if 'out' in kws:
                out = kws.pop('out')
                args += (out,)
            if kws:
                raise TypeError("unexpected keyword arguments to ufunc: %s"
                                % ", ".join(repr(k) for k in sorted(kws)))

        args_len = len(args)
        assert (args_len == nin) or (args_len == nin + self.ufunc.nout)
        assert not kws
        argtys = []
        # To avoid a mismatch in how Numba types values as opposed to
        # Numpy, we need to first check for scalars.  For example, on
        # 64-bit systems, numba.typeof(3) => int32, but
        # np.array(3).dtype => int64.
        for arg in args[:nin]:
            if numpy_support.is_arrayscalar(arg):
                argtys.append(numpy_support.map_arrayscalar_type(arg))
            else:
                argty = typeof(arg)
                if isinstance(argty, types.Array):
                    argty = argty.dtype
                argtys.append(argty)
        return self._compile_for_argtys(tuple(argtys))

    def _compile_for_argtys(self, argtys, return_type=None):
        """
        Given a tuple of argument types (these should be the array
        dtypes, and not the array types themselves), compile the
        element-wise function for those inputs, generate a UFunc loop
        wrapper, and register the loop with the Numpy ufunc object for
        this DUFunc.
        """
        if self._frozen:
            raise RuntimeError("compilation disabled for %s" % (self,))
        assert isinstance(argtys, tuple)
        if return_type is None:
            sig = argtys
        else:
            sig = return_type(*argtys)
        cres, argtys, return_type = ufuncbuilder._compile_element_wise_function(
            self._dispatcher, self.targetoptions, sig)
        actual_sig = ufuncbuilder._finalize_ufunc_signature(
            cres, argtys, return_type)
        dtypenums, ptr, env = ufuncbuilder._build_element_wise_ufunc_wrapper(
            cres, actual_sig)
        self._add_loop(utils.longint(ptr), dtypenums)
        self._keepalive.append((ptr, cres.library, env))
        self._lower_me.libs.append(cres.library)
        return cres

    def _install_type(self, typingctx=None):
        """Constructs and installs a typing class for a DUFunc object in the
        input typing context.  If no typing context is given, then
        _install_type() installs into the typing context of the
        dispatcher object (should be same default context used by
        jit() and njit()).
        """
        if typingctx is None:
            typingctx = self._dispatcher.targetdescr.typing_context
        _ty_cls = type('DUFuncTyping_' + self.ufunc.__name__,
                       (AbstractTemplate,),
                       dict(key=self, generic=self._type_me))
        typingctx.insert_user_function(self, _ty_cls)

    def find_ewise_function(self, ewise_types):
        """
        Given a tuple of element-wise argument types, find a matching
        signature in the dispatcher.

        Return a 2-tuple containing the matching signature, and
        compilation result.  Will return two None's if no matching
        signature was found.
        """
        if self._frozen:
            # If we cannot compile, coerce to the best matching loop
            loop = numpy_support.ufunc_find_matching_loop(self, ewise_types)
            if loop is None:
                return None, None
            ewise_types = tuple(loop.inputs + loop.outputs)[:len(ewise_types)]
        for sig, cres in self._dispatcher.overloads.items():
            if sig.args == ewise_types:
                return sig, cres
        return None, None

    def _type_me(self, argtys, kwtys):
        """
        Implement AbstractTemplate.generic() for the typing class
        built by DUFunc._install_type().

        Return the call-site signature after either validating the
        element-wise signature or compiling for it.
        """
        assert not kwtys
        ufunc = self.ufunc
        _handle_inputs_result = npydecl.Numpy_rules_ufunc._handle_inputs(
            ufunc, argtys, kwtys)
        base_types, explicit_outputs, ndims, layout = _handle_inputs_result
        explicit_output_count = len(explicit_outputs)
        if explicit_output_count > 0:
            ewise_types = tuple(base_types[:-len(explicit_outputs)])
        else:
            ewise_types = tuple(base_types)
        sig, cres = self.find_ewise_function(ewise_types)
        if sig is None:
            # Matching element-wise signature was not found; must
            # compile.
            if self._frozen:
                raise TypeError("cannot call %s with types %s"
                                % (self, argtys))
            self._compile_for_argtys(ewise_types)
            sig, cres = self.find_ewise_function(ewise_types)
            assert sig is not None
        if explicit_output_count > 0:
            outtys = list(explicit_outputs)
        elif ufunc.nout == 1:
            if ndims > 0:
                outtys = [types.Array(sig.return_type, ndims, layout)]
            else:
                outtys = [sig.return_type]
        else:
            raise NotImplementedError("typing gufuncs (nout > 1)")
        outtys.extend(argtys)
        return signature(*outtys)

    def _install_cg(self, targetctx=None):
        """
        Install an implementation function for a DUFunc object in the
        given target context.  If no target context is given, then
        _install_cg() installs into the target context of the
        dispatcher object (should be same default context used by
        jit() and njit()).
        """
        if targetctx is None:
            targetctx = self._dispatcher.targetdescr.target_context
        _any = types.Any
        _arr = types.Array
        # Either all outputs are explicit or none of them are
        sig0 = (_any,) * self.ufunc.nin + (_arr,) * self.ufunc.nout
        sig1 = (_any,) * self.ufunc.nin
        targetctx.insert_func_defn(
            [(self._lower_me, self, sig) for sig in (sig0, sig1)])


array_analysis.MAP_TYPES.append(DUFunc)
