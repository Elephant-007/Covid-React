from warnings import warn
from numba.core import types, config, sigutils
from numba.core.errors import NumbaDeprecationWarning
from .compiler import (compile_device, declare_device_function, Dispatcher,
                       compile_device_template)
from .simulator.kernel import FakeCUDAKernel


_msg_deprecated_signature_arg = ("Deprecated keyword argument `{0}`. "
                                 "Signatures should be passed as the first "
                                 "positional argument.")


def jitdevice(func, link=[], debug=None, inline=False):
    """Wrapper for device-jit.
    """
    debug = config.CUDA_DEBUGINFO_DEFAULT if debug is None else debug
    if link:
        raise ValueError("link keyword invalid for device function")
    return compile_device_template(func, debug=debug, inline=inline)


def jit(func_or_sig=None, argtypes=None, device=False, inline=False,
        link=[], debug=None, **kws):
    """
    JIT compile a python function conforming to the CUDA Python specification.
    If a signature is supplied, then a function is returned that takes a
    function to compile.

    :param func_or_sig: A function to JIT compile, or a signature of a function
       to compile. If a function is supplied, then a
       :class:`numba.cuda.compiler.AutoJitCUDAKernel` is returned. If a
       signature is supplied, then a function is returned. The returned
       function accepts another function, which it will compile and then return
       a :class:`numba.cuda.compiler.AutoJitCUDAKernel`.

       .. note:: A kernel cannot have any return value.
    :param device: Indicates whether this is a device function.
    :type device: bool
    :param bind: (Deprecated) Force binding to CUDA context immediately
    :type bind: bool
    :param link: A list of files containing PTX source to link with the function
    :type link: list
    :param debug: If True, check for exceptions thrown when executing the
       kernel. Since this degrades performance, this should only be used for
       debugging purposes.  Defaults to False.  (The default value can be
       overridden by setting environment variable ``NUMBA_CUDA_DEBUGINFO=1``.)
    :param fastmath: If true, enables flush-to-zero and fused-multiply-add,
       disables precise division and square root. This parameter has no effect
       on device function, whose fastmath setting depends on the kernel function
       from which they are called.
    :param max_registers: Limit the kernel to using at most this number of
       registers per thread. Useful for increasing occupancy.
    """
    debug = config.CUDA_DEBUGINFO_DEFAULT if debug is None else debug

    if link and config.ENABLE_CUDASIM:
        raise NotImplementedError('Cannot link PTX in the simulator')

    if kws.get('boundscheck') == True:
        raise NotImplementedError("bounds checking is not supported for CUDA")

    if argtypes is not None:
        msg = _msg_deprecated_signature_arg.format('argtypes')
        warn(msg, category=NumbaDeprecationWarning)

    if 'bind' in kws:
        msg = _msg_deprecated_signature_arg.format('bind')
        warn(msg, category=NumbaDeprecationWarning)
    else:
        bind=True

    fastmath = kws.get('fastmath', False)
    if argtypes is None and not sigutils.is_signature(func_or_sig):
        if func_or_sig is None:
            if config.ENABLE_CUDASIM:
                def autojitwrapper(func):
                    return FakeCUDAKernel(func, device=device, fastmath=fastmath,
                                          debug=debug)
            else:
                def autojitwrapper(func):
                    return jit(func, device=device, debug=debug, **kws)

            return autojitwrapper
        # func_or_sig is a function
        else:
            if config.ENABLE_CUDASIM:
                return FakeCUDAKernel(func_or_sig, device=device, fastmath=fastmath,
                                      debug=debug)
            elif device:
                return jitdevice(func_or_sig, debug=debug, **kws)
            else:
                targetoptions = kws.copy()
                targetoptions['debug'] = debug
                targetoptions['link'] = link
                sigs = None
                return Dispatcher(func_or_sig, sigs, bind=bind,
                              targetoptions=targetoptions)

    else:
        if config.ENABLE_CUDASIM:
            def jitwrapper(func):
                return FakeCUDAKernel(func, device=device, fastmath=fastmath,
                                      debug=debug)
            return jitwrapper


        if isinstance(func_or_sig, list):
            sigs = func_or_sig
        elif sigutils.is_signature(func_or_sig):
            sigs = [func_or_sig]
        elif func_or_sig is None:
            # Handle the deprecated argtypes / restype specification
            restype = kws.get('restype', types.void)
            sigs = [restype(*argtypes)]
        else:
            from pudb import set_trace; set_trace()
            raise ValueError("Expecting signature or list for signatures")

        for sig in sigs:
            restype, argtypes = convert_types(sig, argtypes)

            if restype and not device and restype != types.void:
                raise TypeError("CUDA kernel must have void return type.")

        def kernel_jit(func):
            targetoptions = kws.copy()
            targetoptions['debug'] = debug
            targetoptions['link'] = link
            return Dispatcher(func, sigs, bind=bind, targetoptions=targetoptions)

        def device_jit(func):
            return compile_device(func, restype, argtypes, inline=inline,
                                  debug=debug)

        if device:
            return device_jit
        else:
            return kernel_jit


def declare_device(name, restype=None, argtypes=None):
    restype, argtypes = convert_types(restype, argtypes)
    return declare_device_function(name, restype, argtypes)


def convert_types(restype, argtypes):
    # eval type string
    if sigutils.is_signature(restype):
        # Commented because we convert restype / argtypes to signature now.
        # Doesn't make too much sense but this will go.
        # assert argtypes is None 
        argtypes, restype = sigutils.normalize_signature(restype)

    return restype, argtypes
