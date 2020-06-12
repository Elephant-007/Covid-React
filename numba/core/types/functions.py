import traceback
from collections import namedtuple, defaultdict
import inspect
import itertools
import logging
import textwrap
from os import path
from shutil import get_terminal_size

from .abstract import Callable, DTypeSpec, Dummy, Literal, Type, weakref
from .common import Opaque
from .misc import unliteral
from numba.core import errors, utils, types, config
import numba

_logger = logging.getLogger(__name__)

# terminal color markup
_termcolor = errors.termcolor()

_FAILURE = namedtuple('_FAILURE', 'template matched error literal')

_termwidth = get_terminal_size().columns


# pull out the lead line as unit tests often use this
_header_lead = "No implementation of function"
_header_template = (_header_lead + " {the_function} found for signature:\n \n "
                    ">>> {fname}({signature})\n \nThere are {ncandidates} "
                    "candidate implementations:")

_reason_template = """
" - Of which {nmatches} did not match due to:\n
"""

def _wrapper(tmp, indent=0):
    return textwrap.indent(tmp, ' ' * indent,  lambda line: True)

_overload_template = ("- Of which {nduplicates} did not match due to:\n"
                      "Overload in function '{function}': File: {file}: "
                      "Line {line}.\n  With argument(s): '({args})':")

_err_reasons = {}
_err_reasons['specific_error'] = ("Rejected as the implementation raised a "
                                  "specific error:\n{}")

def _bt_as_lines(bt):
    """
    Converts a backtrace into a list of lines, squashes it a bit on the way.
    """
    return [y for y in itertools.chain(*[x.split('\n') for x in bt]) if y]

def argsnkwargs_to_str(args, kwargs):
    buf = [str(a) for a in tuple(args)]
    buf.extend(["{}={}".format(k, v) for k, v in kwargs.items()])
    return ', '.join(buf)

class _ResolutionFailures(object):
    """Collect and format function resolution failures.
    """
    def __init__(self, context, function_type, args, kwargs, depth=0):
        self._context = context
        self._function_type = function_type
        self._args = args
        self._kwargs = kwargs
        self._failures = defaultdict(list)
        self._depth = depth
        self._max_depth = 5
        self._scale = 2

    def __len__(self):
        return len(self._failures)

    def add_error(self, calltemplate, matched, error, literal):
        """
        Args
        ----
        calltemplate : CallTemplate
        error : Exception or str
            Error message
        """
        isexc = isinstance(error, Exception)
        errclazz = '%s: ' % type(error).__name__ if isexc else ''

        key = "{}{}".format(errclazz, str(error))
        self._failures[key].append(_FAILURE(calltemplate, matched, error,
                                            literal))

    def _get_source_info(self, source_fn):
        source_line = "<N/A>"
        if 'builtin_function' in str(type(source_fn)):
            source_file = "<built-in>"
        else:
            try:
                source_file = path.abspath(inspect.getsourcefile(source_fn))
                source_line = inspect.getsourcelines(source_fn)[1]
                here = path.abspath(__file__)
                common = path.commonpath([here, source_file])
                source_file = source_file.replace(common, 'numba')
            except:
                source_file = "Unknown"
        return source_file, source_line

    def format(self):
        """Return a formatted error message from all the gathered errors.
        """
        indent = ' ' * self._scale
        argstr = argsnkwargs_to_str(self._args, self._kwargs)
        ncandidates = sum([len(x) for x in self._failures.values()])

        # sort out a display name for the function
        tykey = self._function_type.typing_key
        # most things have __name__
        fname = getattr(tykey, '__name__', None)
        is_external_fn_ptr = isinstance(self._function_type,
                                        ExternalFunctionPointer)
        if fname is None:
            if is_external_fn_ptr:
                fname = "ExternalFunctionPointer"
            else:
                fname = "<unknown function>"

        msgbuf = [_header_template.format(the_function=self._function_type,
                                          fname=fname,
                                          signature=argstr,
                                          ncandidates=ncandidates)]
        nolitargs = tuple([unliteral(a) for a in self._args])
        nolitkwargs = {k: unliteral(v) for k, v in self._kwargs.items()}
        nolitargstr = argsnkwargs_to_str(nolitargs, nolitkwargs)

        key = self._function_type.key[0]
        fn_name = getattr(key, '__name__', str(key))

        # depth could potentially get massive, so limit it.
        ldepth = min(max(self._depth, 0), self._max_depth)

        for i, (k, err_list) in enumerate(self._failures.items()):
            err = err_list[0]
            nduplicates = len(err_list)
            template, error = err.template, err.error
            source_fn = template.key
            if isinstance(source_fn, numba.core.extending._Intrinsic):
                source_fn = template.key._defn
            elif (is_external_fn_ptr and
                  isinstance(source_fn, numba.core.typing.templates.Signature)):
                source_fn = template.__class__
            elif hasattr(template, '_overload_func'):
                source_fn = template._overload_func
            source_file, source_line = self._get_source_info(source_fn)
            largstr = argstr if err.literal else nolitargstr
            msgbuf.append(_termcolor.errmsg(
                _wrapper(_overload_template.format(nduplicates=nduplicates,
                                                  function=source_fn.__name__,
                                                  file=source_file,
                                                  line=source_line,
                                                  args=largstr),
                ldepth + 1)))

            if isinstance(error, BaseException):
                reason = indent + self.format_error(error)
                errstr = _err_reasons['specific_error'].format(reason)
            else:
                errstr = error
            # if you are a developer, show the back traces
            if config.DEVELOPER_MODE:
                if isinstance(error, BaseException):
                    # if the error is an actual exception instance, trace it
                    bt = traceback.format_exception(type(error), error,
                                                    error.__traceback__)
                else:
                    bt = [""]
                bt_as_lines = _bt_as_lines(bt)
                nd2indent = '\n{}'.format(2 * indent)
                errstr += _termcolor.reset(nd2indent +
                                            nd2indent.join(bt_as_lines))
            msgbuf.append(_termcolor.highlight(_wrapper(errstr, ldepth + 2)))
            loc = self.get_loc(template, error)
            if loc:
                msgbuf.append('{}raised from {}'.format(indent, loc))

        # the commented bit rewraps each block, may not be helpful?!
        return _wrapper('\n'.join(msgbuf) + '\n') # , self._scale * ldepth)

    def format_error(self, error):
        """Format error message or exception
        """
        if isinstance(error, Exception):
            return '{}: {}'.format(type(error).__name__, error)
        else:
            return '{}'.format(error)

    def get_loc(self, classtemplate, error):
        """Get source location information from the error message.
        """
        if isinstance(error, Exception) and hasattr(error, '__traceback__'):
            # traceback is unavailable in py2
            frame = traceback.extract_tb(error.__traceback__)[-1]
            return "{}:{}".format(frame[0], frame[1])

    def raise_error(self):
        for faillist in self._failures.values():
            for fail in faillist:
                if isinstance(fail.error, errors.ForceLiteralArg):
                    raise fail.error
        raise errors.TypingError(self.format())


class BaseFunction(Callable):
    """
    Base type class for some function types.
    """

    def __init__(self, template):

        if isinstance(template, (list, tuple)):
            self.templates = tuple(template)
            keys = set(temp.key for temp in self.templates)
            if len(keys) != 1:
                raise ValueError("incompatible templates: keys = %s"
                                 % (keys,))
            self.typing_key, = keys
        else:
            self.templates = (template,)
            self.typing_key = template.key
        self._impl_keys = {}
        name = "%s(%s)" % (self.__class__.__name__, self.typing_key)
        self._depth = 0
        super(BaseFunction, self).__init__(name)

    @property
    def key(self):
        return self.typing_key, self.templates

    def augment(self, other):
        """
        Augment this function type with the other function types' templates,
        so as to support more input types.
        """
        if type(other) is type(self) and other.typing_key == self.typing_key:
            return type(self)(self.templates + other.templates)

    def get_impl_key(self, sig):
        """
        Get the implementation key (used by the target context) for the
        given signature.
        """
        return self._impl_keys[sig.args]

    def get_call_type(self, context, args, kws):
        failures = _ResolutionFailures(context, self, args, kws,
                                       depth=self._depth)
        self._depth += 1
        for temp_cls in self.templates:
            temp = temp_cls(context)
            for uselit in [True, False]:
                try:
                    if uselit:
                        sig = temp.apply(args, kws)
                    else:
                        nolitargs = tuple([unliteral(a) for a in args])
                        nolitkws = {k: unliteral(v) for k, v in kws.items()}
                        sig = temp.apply(nolitargs, nolitkws)
                except Exception as e:
                    sig = None
                    failures.add_error(temp, False, e, uselit)
                else:
                    if sig is not None:
                        self._impl_keys[sig.args] = temp.get_impl_key(sig)
                        self._depth -= 1
                        return sig
                    else:
                        registered_sigs = getattr(temp, 'cases', None)
                        if registered_sigs is not None:
                            msg = "No match for registered cases:\n%s"
                            msg = msg % '\n'.join(" * {}".format(x) for x in
                                                  registered_sigs)
                        else:
                            msg = 'No match.'
                        failures.add_error(temp, True, msg, uselit)

        if len(failures) == 0:
            raise AssertionError("Internal Error. "
                                 "Function resolution ended with no failures "
                                 "or successful signature")
        failures.raise_error()

    def get_call_signatures(self):
        sigs = []
        is_param = False
        for temp in self.templates:
            sigs += getattr(temp, 'cases', [])
            is_param = is_param or hasattr(temp, 'generic')
        return sigs, is_param


class Function(BaseFunction, Opaque):
    """
    Type class for builtin functions implemented by Numba.
    """


class BoundFunction(Callable, Opaque):
    """
    A function with an implicit first argument (denoted as *this* below).
    """

    def __init__(self, template, this):
        # Create a derived template with an attribute *this*
        newcls = type(template.__name__ + '.' + str(this), (template,),
                      dict(this=this))
        self.template = newcls
        self.typing_key = self.template.key
        self.this = this
        name = "%s(%s for %s)" % (self.__class__.__name__,
                                  self.typing_key, self.this)
        super(BoundFunction, self).__init__(name)

    def unify(self, typingctx, other):
        if (isinstance(other, BoundFunction) and
            self.typing_key == other.typing_key):
            this = typingctx.unify_pairs(self.this, other.this)
            if this is not None:
                # XXX is it right that both template instances are distinct?
                return self.copy(this=this)

    def copy(self, this):
        return type(self)(self.template, this)

    @property
    def key(self):
        return self.typing_key, self.this

    def get_impl_key(self, sig):
        """
        Get the implementation key (used by the target context) for the
        given signature.
        """
        return self.typing_key

    def get_call_type(self, context, args, kws):
        template = self.template(context)
        literal_e = None
        nonliteral_e = None


        # Try with Literal
        try:
            out = template.apply(args, kws)
        except Exception as exc:
            if isinstance(exc, errors.ForceLiteralArg):
                raise exc
            literal_e = exc
            out = None

        # if the unliteral_args and unliteral_kws are the same as the literal
        # ones, set up to not bother retrying
        unliteral_args = tuple([unliteral(a) for a in args])
        unliteral_kws = {k: unliteral(v) for k, v in kws.items()}
        skip = unliteral_args == args and kws == unliteral_kws

        # If the above template application failed and the non-literal args are
        # different to the literal ones, try again with literals rewritten as
        # non-literals
        if not skip and out is None:
            try:
                out = template.apply(unliteral_args, unliteral_kws)
            except Exception as exc:
                if isinstance(exc, errors.ForceLiteralArg):
                    raise exc
                nonliteral_e = exc

        if out is None and (nonliteral_e is not None or literal_e is not None):
            header = "- Resolution failure for {} arguments:\n{}\n"
            tmplt = _termcolor.highlight(header)
            if config.DEVELOPER_MODE:
                indent = ' ' * 4
                def add_bt(error):
                    if isinstance(error, BaseException):
                        # if the error is an actual exception instance, trace it
                        bt = traceback.format_exception(type(error), error,
                                                        error.__traceback__)
                    else:
                        bt = [""]
                    nd2indent = '\n{}'.format(2 * indent)
                    errstr += _termcolor.reset(nd2indent +
                                               nd2indent.join(bt_as_lines))
                    return _termcolor.reset(errstr)
            else:
                add_bt = lambda X: ''

            def nested_msg(literalness, e):
                estr = str(e)
                estr = estr if estr else (str(repr(e)) + add_bt(e))
                new_e = errors.TypingError(textwrap.dedent(estr))
                return tmplt.format(literalness, str(new_e))

            raise errors.TypingError(nested_msg('literal', literal_e) +
                                     nested_msg('non-literal', nonliteral_e))
        return out


    def get_call_signatures(self):
        sigs = getattr(self.template, 'cases', [])
        is_param = hasattr(self.template, 'generic')
        return sigs, is_param

class MakeFunctionLiteral(Literal, Opaque):
    pass


class WeakType(Type):
    """
    Base class for types parametered by a mortal object, to which only
    a weak reference is kept.
    """

    def _store_object(self, obj):
        self._wr = weakref.ref(obj)

    def _get_object(self):
        obj = self._wr()
        if obj is None:
            raise ReferenceError("underlying object has vanished")
        return obj

    @property
    def key(self):
        return self._wr

    def __eq__(self, other):
        if type(self) is type(other):
            obj = self._wr()
            return obj is not None and obj is other._wr()

    def __hash__(self):
        return Type.__hash__(self)


class Dispatcher(WeakType, Callable, Dummy):
    """
    Type class for @jit-compiled functions.
    """

    def __init__(self, dispatcher):
        self._store_object(dispatcher)
        super(Dispatcher, self).__init__("type(%s)" % dispatcher)

    def dump(self, tab=''):
        print(f'{tab}DUMP {type(self).__name__}[code={self._code}, name={self.name}]')
        self.dispatcher.dump(tab=tab + '  ')
        print(f'{tab}END DUMP')

    def get_call_type(self, context, args, kws):
        """
        Resolve a call to this dispatcher using the given argument types.
        A signature returned and it is ensured that a compiled specialization
        is available for it.
        """
        template, pysig, args, kws = self.dispatcher.get_call_template(args, kws)
        sig = template(context).apply(args, kws)
        if sig:
            sig = sig.replace(pysig=pysig)
            return sig

    def get_call_signatures(self):
        sigs = self.dispatcher.nopython_signatures
        return sigs, True

    @property
    def dispatcher(self):
        """
        A strong reference to the underlying numba.dispatcher.Dispatcher instance.
        """
        return self._get_object()

    def get_overload(self, sig):
        """
        Get the compiled overload for the given signature.
        """
        return self.dispatcher.get_overload(sig.args)

    def get_impl_key(self, sig):
        """
        Get the implementation key for the given signature.
        """
        return self.get_overload(sig)

    def unify(self, context, other):
        return utils.unified_function_type((self, other), require_precise=False)


class ObjModeDispatcher(Dispatcher):
    """Dispatcher subclass that enters objectmode function.
    """
    pass


class ExternalFunctionPointer(BaseFunction):
    """
    A pointer to a native function (e.g. exported via ctypes or cffi).
    *get_pointer* is a Python function taking an object
    and returning the raw pointer value as an int.
    """
    def __init__(self, sig, get_pointer, cconv=None):
        from numba.core.typing.templates import (AbstractTemplate,
                                                 make_concrete_template,
                                                 signature)
        from numba.core.types import ffi_forced_object
        if sig.return_type == ffi_forced_object:
            raise TypeError("Cannot return a pyobject from a external function")
        self.sig = sig
        self.requires_gil = any(a == ffi_forced_object for a in self.sig.args)
        self.get_pointer = get_pointer
        self.cconv = cconv
        if self.requires_gil:
            class GilRequiringDefn(AbstractTemplate):
                key = self.sig

                def generic(self, args, kws):
                    if kws:
                        raise TypeError("does not support keyword arguments")
                    # Make ffi_forced_object a bottom type to allow any type to be
                    # casted to it. This is the only place that support
                    # ffi_forced_object.
                    coerced = [actual if formal == ffi_forced_object else formal
                               for actual, formal
                               in zip(args, self.key.args)]
                    return signature(self.key.return_type, *coerced)
            template = GilRequiringDefn
        else:
            template = make_concrete_template("CFuncPtr", sig, [sig])
        super(ExternalFunctionPointer, self).__init__(template)

    @property
    def key(self):
        return self.sig, self.cconv, self.get_pointer


class ExternalFunction(Function):
    """
    A named native function (resolvable by LLVM) accepting an explicit signature.
    For internal use only.
    """

    def __init__(self, symbol, sig):
        from numba.core import typing
        self.symbol = symbol
        self.sig = sig
        template = typing.make_concrete_template(symbol, symbol, [sig])
        super(ExternalFunction, self).__init__(template)

    @property
    def key(self):
        return self.symbol, self.sig


class NamedTupleClass(Callable, Opaque):
    """
    Type class for namedtuple classes.
    """

    def __init__(self, instance_class):
        self.instance_class = instance_class
        name = "class(%s)" % (instance_class)
        super(NamedTupleClass, self).__init__(name)

    def get_call_type(self, context, args, kws):
        # Overridden by the __call__ constructor resolution in typing.collections
        return None

    def get_call_signatures(self):
        return (), True

    @property
    def key(self):
        return self.instance_class


class NumberClass(Callable, DTypeSpec, Opaque):
    """
    Type class for number classes (e.g. "np.float64").
    """

    def __init__(self, instance_type):
        self.instance_type = instance_type
        name = "class(%s)" % (instance_type,)
        super(NumberClass, self).__init__(name)

    def get_call_type(self, context, args, kws):
        # Overridden by the __call__ constructor resolution in typing.builtins
        return None

    def get_call_signatures(self):
        return (), True

    @property
    def key(self):
        return self.instance_type

    @property
    def dtype(self):
        return self.instance_type


class RecursiveCall(Opaque):
    """
    Recursive call to a Dispatcher.
    """
    _overloads = None

    def __init__(self, dispatcher_type):
        assert isinstance(dispatcher_type, Dispatcher)
        self.dispatcher_type = dispatcher_type
        name = "recursive(%s)" % (dispatcher_type,)
        super(RecursiveCall, self).__init__(name)
        # Initializing for the first time
        if self._overloads is None:
            self._overloads = {}

    @property
    def overloads(self):
        return self._overloads

    @property
    def key(self):
        return self.dispatcher_type
