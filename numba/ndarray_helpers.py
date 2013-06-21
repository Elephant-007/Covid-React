# -*- coding: utf-8 -*-
from __future__ import print_function, division, absolute_import
# For reference:
#    typedef struct {
#    PyObject_HEAD                   // indices (skipping the head)
#    char *data;                     // 0
#    int nd;                         // 1
#    int *dimensions, *strides;      // 2, 3
#    PyObject *base;                 // 4
#    PyArray_Descr *descr;           // 5
#    int flags;                      // 6
#    } PyArrayObject;

from numba import *
from numba.typesystem import tbaa
from numba.llvm_types import _head_len, _int32, _LLVMCaster, constant_int
import llvm.core as lc

def _const_int(X):
    return lc.Constant.int(lc.Type.int(), X)

def ptr_at(builder, ptr, idx):
    return builder.gep(ptr, [_const_int(idx)])

def load_at(builder, ptr, idx):
    return builder.load(ptr_at(builder, ptr, idx))

def store_at(builder, ptr, idx, val):
    builder.store(val, ptr_at(builder, ptr, idx))


def set_metadata(tbaa, instr, type):
    if type is not None:
        metadata = tbaa.get_metadata(type)
        instr.set_metadata("tbaa", metadata)

def make_property(type=None, invariant=True):
    """
    type: The type to be used for TBAA annotation
    """
    def decorator(access_func):
        def load(self):
            instr = self.builder.load(access_func(self))
            if self.tbaa:
                set_metadata(self.tbaa, instr, type)
            return instr

        def store(self, value):
            ptr = access_func(self)
            instr = self.builder.store(value, ptr)
            if self.tbaa:
                set_metadata(self.tbaa, instr, type)

        return property(load, store)

    return decorator

class PyArrayAccessor(object):
    """
    Convenient access to a the native fields of a NumPy array.

    builder: llvmpy IRBuilder
    pyarray_ptr: pointer to the numpy array
    tbaa: metadata.TBAAMetadata instance
    """

    def __init__(self, builder, pyarray_ptr, tbaa=None, dtype=None):
        self.builder = builder
        self.pyarray_ptr = pyarray_ptr
        self.tbaa = tbaa # this may be None
        self.dtype = dtype

    def _get_element(self, idx):
        indices = [constant_int(0), constant_int(_head_len + idx)]
        ptr = self.builder.gep(self.pyarray_ptr, indices)
        return ptr

    def get_data(self):
        instr = self.builder.load(self._get_element(0))
        if self.tbaa:
            set_metadata(self.tbaa, instr, self.dtype.pointer())
        return instr

    def set_data(self, value):
        instr = self.builder.store(value, self._get_element(0))
        if self.tbaa:
            set_metadata(self.tbaa, instr, self.dtype.pointer())

    data = property(get_data, set_data, "The array.data attribute")

    def typed_data(self, context):
        data = self.data
        ltype = self.dtype.pointer().to_llvm(context)
        return self.builder.bitcast(data, ltype)

    @make_property(tbaa.numpy_ndim)
    def ndim(self):
        return self._get_element(1)

    @make_property(tbaa.numpy_shape.pointer().qualify("const"))
    def dimensions(self):
        return self._get_element(2)

    shape = dimensions

    @make_property(tbaa.numpy_strides.pointer().qualify("const"))
    def strides(self):
        return self._get_element(3)

    @make_property(tbaa.numpy_base)
    def base(self):
        return self._get_element(4)

    @make_property(tbaa.numpy_dtype)
    def descr(self):
        return self._get_element(5)

    @make_property(tbaa.numpy_flags)
    def flags(self):
        return self._get_element(6)

class NumpyArray(object):
    """
    LLArray compatible inferface for NumPy's ndarray
    """

    _strides_ptr = None
    _strides = None
    _shape_ptr = None
    _shape = None
    _data_ptr = None
    _freefuncs = []
    _freedata = []

    def __init__(self, pyarray_ptr, builder, tbaa=None, type=None):
        self.type = type
        self.nd = type.ndim

        # LLVM attributes
        self.arr = PyArrayAccessor(builder, pyarray_ptr, tbaa, type.dtype)
        self.builder = builder
        self._shape = None
        self._strides = None
        self.caster = _LLVMCaster(builder)

    @property
    def data(self):
        if not self._data_ptr:
            self._data_ptr = self.arr.get_data()
        return self._data_ptr

    @property
    def shape_ptr(self):
        return self._shape_ptr

    @property
    def strides_ptr(self):
        return self._strides_ptr

    @property
    def shape(self):
        if not self._shape:
            self._shape_ptr = self.arr.shape
            self._shape = self.preload(self._shape_ptr, self.nd)
        return self._shape

    @property
    def strides(self):
        if not self._strides:
            self._strides_ptr = self.arr.strides
            self._strides = self.preload(self._strides_ptr, self.nd)
        return self._strides

    # def setstrides(self, p_strides, strides=None):
    #     self._strides_ptr = p_strides
    #     self._strides = strides

    @property
    def itemsize(self):
        raise NotImplementedError

    def preload(self, ptr, count=None):
        assert count is not None
        return [load_at(self.builder, ptr, i) for i in range(count)]

    def getptr(self, *indices):
        offset = _const_int(0)
        for i, (stride, index) in enumerate(zip(self.strides, indices)):
            index = self.caster.cast(index, stride.type, unsigned=False)
            offset = self.caster.cast(offset, stride.type, unsigned=False)
            offset = self.builder.add(offset, self.builder.mul(index, stride))

        data_ty = self.type.dtype.to_llvm()
        data_ptr_ty = lc.Type.pointer(data_ty)

        dptr_plus_offset = self.builder.gep(self.data, [offset])

        ptr = self.builder.bitcast(dptr_plus_offset, data_ptr_ty)
        return ptr