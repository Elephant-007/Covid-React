from llvm.core import Type, Constant
import llvm.ee as le
from llvm import LLVMException
import ctypes
from numba import types, utils

_PyNone = ctypes.c_ssize_t(id(None))


@utils.runonce
def fix_python_api():
    le.dylib_add_symbol("Py_None", ctypes.addressof(_PyNone))


class PythonAPI(object):
    def __init__(self, context, builder, globalscope=None):
        fix_python_api()
        self.context = context
        self.builder = builder
        self.pyobj = self.context.get_argument_type(types.pyobject)
        self.long = Type.int(ctypes.sizeof(ctypes.c_long) * 8)
        self.double = Type.double()
        self.py_ssize_t = self.context.get_value_type(types.intp)
        self.cstring = Type.pointer(Type.int(8))
        self.module = builder.basic_block.function.module
        self.globalscope = globalscope

    # ------ Python API -----

    def incref(self, obj):
        fnty = Type.function(Type.void(), [self.pyobj])
        fn = self._get_function(fnty, name="Py_IncRef")
        self.builder.call(fn, [obj])

    def decref(self, obj):
        fnty = Type.function(Type.void(), [self.pyobj])
        fn = self._get_function(fnty, name="Py_DecRef")
        self.builder.call(fn, [obj])

    def parse_tuple_and_keywords(self, args, kws, fmt, keywords, *objs):
        charptr = Type.pointer(Type.int(8))
        charptrary = Type.pointer(charptr)
        argtypes = [self.pyobj, self.pyobj, charptr, charptrary]
        fnty = Type.function(Type.int(), argtypes, var_arg=True)
        fn = self._get_function(fnty, name="PyArg_ParseTupleAndKeywords")
        return self.builder.call(fn, [args, kws, fmt, keywords] + list(objs))

    def dict_getitem_string(self, dic, name):
        """Returns a borrowed reference
        """
        fnty = Type.function(self.pyobj, [self.pyobj, self.cstring])
        fn = self._get_function(fnty, name="PyDict_GetItemString")
        cstr = self.context.insert_const_string(self.module, name)
        return self.builder.call(fn, [dic, cstr])

    def err_clear(self):
        fnty = Type.function(Type.void(), ())
        fn = self._get_function(fnty, name="PyErr_Clear")
        return self.builder.call(fn, ())

    def import_module_noblock(self, modname):
        fnty = Type.function(self.pyobj, [self.cstring])
        fn = self._get_function(fnty, name="PyImport_ImportModuleNoBlock")
        return self.builder.call(fn, [modname])

    def call_function_objargs(self, callee, objargs):
        fnty = Type.function(self.pyobj, [self.pyobj], var_arg=True)
        fn = self._get_function(fnty, name="PyObject_CallFunctionObjArgs")
        args = [callee] + list(objargs)
        args.append(self.context.get_constant_null(types.pyobject))
        return self.builder.call(fn, args)

    def long_from_long(self, ival):
        fnty = Type.function(self.pyobj, [self.long])
        fn = self._get_function(fnty, name="PyLong_FromLong")
        return self.builder.call(fn, [ival])

    def long_from_ssize_t(self, ival):
        fnty = Type.function(self.pyobj, [self.long])
        fn = self._get_function(fnty, name="PyLong_FromSsize_t")
        return self.builder.call(fn, [ival])

    def float_from_double(self, fval):
        fnty = Type.function(self.pyobj, [self.double])
        fn = self._get_function(fnty, name="PyFloat_FromDouble")
        return self.builder.call(fn, [fval])

    def number_as_ssize_t(self, numobj):
        fnty = Type.function(self.py_ssize_t, [self.pyobj])
        fn = self._get_function(fnty, name="PyNumber_AsSsize_t")
        return self.builder.call(fn, [numobj])

    def _get_number_operator(self, name):
        fnty = Type.function(self.pyobj, [self.pyobj, self.pyobj])
        fn = self._get_function(fnty, name="PyNumber_%s" % name)
        return fn

    def number_add(self, lhs, rhs):
        fn = self._get_number_operator("Add")
        return self.builder.call(fn, [lhs, rhs])

    def number_subtract(self, lhs, rhs):
        fn = self._get_number_operator("Subtract")
        return self.builder.call(fn, [lhs, rhs])

    def number_multiply(self, lhs, rhs):
        fn = self._get_number_operator("Multiply")
        return self.builder.call(fn, [lhs, rhs])

    def number_divide(self, lhs, rhs):
        fn = self._get_number_operator("Divide")
        return self.builder.call(fn, [lhs, rhs])

    def number_float(self, val):
        fnty = Type.function(self.pyobj, [self.pyobj])
        fn = self._get_function(fnty, name="PyNumber_Float")
        return self.builder.call(fn, [val])

    def float_as_double(self, fobj):
        fnty = Type.function(self.double, [self.pyobj])
        fn = self._get_function(fnty, name="PyFloat_AsDouble")
        return self.builder.call(fn, [fobj])

    def object_istrue(self, obj):
        fnty = Type.function(Type.int(), [self.pyobj])
        fn = self._get_function(fnty, name="PyObject_IsTrue")
        return self.builder.call(fn, [obj])

    def object_richcompare(self, lhs, rhs, opstr):
        """
        Refer to Python source Include/object.h for macros definition
        of the opid.
        """
        ops = ['<', '<=', '==', '!=', '>', '>=']
        opid = ops.index(opstr)
        assert 0 <= opid < len(ops)
        fnty = Type.function(self.pyobj, [self.pyobj, self.pyobj, Type.int()])
        fn = self._get_function(fnty, name="PyObject_RichCompare")
        lopid = self.context.get_constant(types.int32, opid)
        return self.builder.call(fn, (lhs, rhs, lopid))

    def bool_from_long(self, ival):
        fnty = Type.function(self.pyobj, [self.long])
        fn = self._get_function(fnty, name="PyBool_FromLong")
        return self.builder.call(fn, [ival])

    def object_getattr_string(self, obj, attr):
        cstr = self.context.insert_const_string(self.module, attr)
        fnty = Type.function(self.pyobj, [self.pyobj, self.cstring])
        fn = self._get_function(fnty, name="PyObject_GetAttrString")
        return self.builder.call(fn, [obj, cstr])

    def string_as_string(self, strobj):
        fnty = Type.function(self.cstring, [self.pyobj])
        fn = self._get_function(fnty, name="PyString_AsString")
        return self.builder.call(fn, [strobj])

    def string_from_string_and_size(self, string):
        fnty = Type.function(self.pyobj, [self.cstring, self.py_ssize_t])
        fn = self._get_function(fnty, name="PyString_FromStringAndSize")
        cstr = self.context.insert_const_string(self.module, string)
        sz = self.context.get_constant(types.intp, len(string))
        return self.builder.call(fn, [cstr, sz])

    def object_str(self, obj):
        fnty = Type.function(self.pyobj, [self.pyobj])
        fn = self._get_function(fnty, name="PyObject_Str")
        return self.builder.call(fn, [obj])

    def tuple_getitem(self, tup, idx):
        """
        Borrow reference
        """
        fnty = Type.function(self.pyobj, [self.pyobj, self.py_ssize_t])
        fn = self._get_function(fnty, "PyTuple_GetItem")
        idx = self.context.get_constant(types.intp, idx)
        return self.builder.call(fn, [tup, idx])

    def make_none(self):
        obj = self._get_object("Py_None")
        self.incref(obj)
        return obj

    # ------ utils -----

    def _get_object(self, name):
        try:
            gv = self.module.get_global_variable_named(name)
        except LLVMException:
            gv = self.module.add_global_variable(self.pyobj, name)
        return self.builder.load(gv)

    def _get_function(self, fnty, name):
        return self.module.get_or_insert_function(fnty, name=name)

    def alloca_obj(self):
        return self.builder.alloca(self.pyobj)

    def print_object(self, obj):
        strobj = self.object_str(obj)
        cstr = self.string_as_string(strobj)
        self.context.print_string(self.builder, cstr)
        self.decref(str)

    def get_null_object(self):
        return Constant.null(self.pyobj)

    def to_native_arg(self, obj, typ):
        if typ == types.pyobject:
            return obj
        elif typ == types.int32:
            ssize_val = self.number_as_ssize_t(obj)
            return self.builder.trunc(ssize_val,
                                      self.context.get_argument_type(typ))
        elif typ == types.float32:
            fobj = self.number_float(obj)
            fval = self.float_as_double(fobj)
            self.decref(fobj)
            return self.builder.fptrunc(fval,
                                        self.context.get_argument_type(typ))

        elif typ == types.float64:
            fobj = self.number_float(obj)
            fval = self.float_as_double(fobj)
            self.decref(fobj)
            return fval

        elif isinstance(typ, types.Array):
            return self.to_native_array(typ, obj)
        raise NotImplementedError(typ)

    def from_native_return(self, val, typ):
        if typ == types.pyobject:
            return val
        elif typ == types.boolean:
            longval = self.builder.zext(val, self.long)
            return self.bool_from_long(longval)

        elif typ == types.int32:
            longval = self.builder.sext(val, self.long)
            return self.long_from_long(longval)

        elif typ == types.float32:
            dbval = self.builder.fpext(val, self.double)
            return self.float_from_double(val)

        elif typ == types.float64:
            return self.float_from_double(val)

        elif typ == types.none:
            ret = self.make_none()
            return ret
        raise NotImplementedError(typ)

    def to_native_array(self, typ, ary):
        nativearycls = self.context.make_array(typ)
        nativeary = nativearycls(self.context, self.builder)
        ctobj = self.object_getattr_string(ary, "ctypes")
        cdata = self.object_getattr_string(ctobj, "data")
        pyshape = self.object_getattr_string(ary, "shape")
        pystrides = self.object_getattr_string(ary, "strides")

        rawint = self.number_as_ssize_t(cdata)

        nativeary.data = self.builder.inttoptr(rawint, nativeary.data.type)

        shapeary = nativeary.shape
        strideary = nativeary.strides

        for i in range(typ.ndim):
            shape = self.tuple_getitem(pyshape, i)
            stride = self.tuple_getitem(pystrides, i)

            shapeval = self.number_as_ssize_t(shape)
            strideval = self.number_as_ssize_t(stride)

            shapeary = self.builder.insert_value(shapeary, shapeval, i)
            strideary = self.builder.insert_value(strideary, strideval, i)

        nativeary.shape = shapeary
        nativeary.strides = strideary

        self.decref(cdata)
        self.decref(pyshape)
        self.decref(pystrides)

        return nativeary._getvalue()

