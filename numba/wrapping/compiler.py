import inspect

from numba import typesystem
import numba.pipeline
from numba.exttypes import virtual
from numba.exttypes import signatures
import numba.exttypes.entrypoints

import numba.decorators

def resolve_argtypes(env, py_func, template_signature,
                     args, kwargs, translator_kwargs):
    """
    Given an autojitting numba function, return the argument types.
    These need to be resolved in order for the function cache to work.

    TODO: have a single entry point that resolves the argument types!
    """
    assert not kwargs, "Keyword arguments are not supported yet"

    locals_dict = translator_kwargs.get("locals", None)

    argcount = py_func.__code__.co_argcount
    if argcount != len(args):
        if argcount == 1:
            arguments = 'argument'
        else:
            arguments = 'arguments'
        raise TypeError("%s() takes exactly %d %s (%d given)" % (
                                py_func.__name__, argcount,
                                arguments, len(args)))

    return_type = None
    argnames = inspect.getargspec(py_func).args
    argtypes = [env.context.typemapper.from_python(x) for x in args]

    if template_signature is not None:
        template_context, signature = typesystem.resolve_templates(
                locals_dict, template_signature, argnames, argtypes)
        return_type = signature.return_type
        argtypes = list(signature.args)

    if locals_dict is not None:
        for i, argname in enumerate(argnames):
            if argname in locals_dict:
                new_type = locals_dict[argname]
                argtypes[i] = new_type

    return typesystem.function(return_type, tuple(argtypes))

class Compiler(object):

    def __init__(self, env, py_func, nopython, flags, template_signature):
        self.env = env
        self.py_func = py_func
        self.nopython = nopython
        self.flags = flags
        self.target = flags.pop('target', 'cpu')
        self.template_signature = template_signature

    def resolve_argtypes(self, args, kwargs):
        signature = resolve_argtypes(self.env, self.py_func,
                                     self.template_signature,
                                     args, kwargs, self.flags)
        return signature

    def compile_from_args(self, args, kwargs):
        signature = self.resolve_argtypes(args, kwargs)
        return self.compile(signature)

    def compile(self, signature):
        "Compile the Python function with the given signature"

class FunctionCompiler(Compiler):

    def compile(self, signature):
        jitter = numba.decorators.jit_targets[(self.target, 'ast')]

        dec = jitter(restype=signature.return_type,
                     argtypes=signature.args,
                     target=self.target, nopython=self.nopython,
                     env=self.env, **self.flags)

        compiled_function = dec(self.py_func)
        return compiled_function

class ClassCompiler(Compiler):

    def __init__(self, *args, **kwargs):
        super(ClassCompiler, self).__init__(*args, **kwargs)

        # from numba.exttypes.autojitclass import create_extension_compiler
        # self.extension_compiler = create_extension_compiler(
        #     self.env, self.py_func, self.flags)

    def resolve_argtypes(self, args, kwargs):
        assert not kwargs
        argtypes = map(self.env.context.typemapper.from_python, args)
        signature = typesystem.function(None, argtypes)
        return signature

    def compile(self, signature):
        py_class = self.py_func
        return numba.exttypes.entrypoints.autojit_extension_class(
            self.env, py_class, self.flags, signature.args)

#------------------------------------------------------------------------
# Autojit Method Compiler
#------------------------------------------------------------------------

def autojit_method_compiler(env, extclass, method, signature):
    """
    Called to compile a new specialized method. The result should be
    added to the perfect hash-based vtable.
    """
    # compiled_method = numba.jit(argtypes=argtypes)(method.py_func)
    func_env = numba.pipeline.compile2(env, method.py_func,
                                       restype=signature.return_type,
                                       argtypes=signature.args)

    # Create Method for the specialization
    new_method = signatures.Method(
        method.py_func,
        method.name,
        func_env.func_signature,
        is_class=method.is_class,
        is_static=method.is_static)

    new_method.update_from_env(func_env)

    # Update vtable type
    vtable_wrapper = extclass.__numba_vtab
    vtable_type = extclass.exttype.vtab_type
    vtable_type.specialized_methods[new_method.name,
                                    signature.args] = new_method

    # Replace vtable (which will update the vtable all (live) objects use)
    new_vtable = virtual.build_hashing_vtab(vtable_type)
    vtable_wrapper.replace_vtable(new_vtable)

    return func_env.numba_wrapper_func

class MethodCompiler(Compiler):

    def __init__(self, env, extclass, method, flags=None):
        super(MethodCompiler, self).__init__(env, method.py_func,
                                             method.nopython, flags or {},
                                             method.template_signature)
        self.extclass = extclass
        self.method = method

    def compile(self, signature):
        return autojit_method_compiler(
            self.env, self.extclass, self.method, signature)
