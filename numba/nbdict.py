"""
Python wrapper that connects CPython interpreter to the numba dictobject.
"""
from collections import MutableMapping

from numba.types import DictType
from numba import njit, dictobject


@njit
def _make_dict(keyty, valty):
    return dictobject._box(dictobject.new_dict(keyty, valty))


@njit
def _length(opaque):
    d = dictobject._unbox(*opaque)
    return len(d)


@njit
def _setitem(opaque, key, value):
    d = dictobject._unbox(*opaque)
    d[key] = value


@njit
def _getitem(opaque, key):
    d = dictobject._unbox(*opaque)
    return d[key]


@njit
def _delitem(opaque, key):
    d = dictobject._unbox(*opaque)
    del d[key]


@njit
def _contains(opaque, key):
    d = dictobject._unbox(*opaque)
    return key in d


@njit
def _get(opaque, key, default):
    d = dictobject._unbox(*opaque)
    return d.get(key, default)


@njit
def _setdefault(opaque, key, default):
    d = dictobject._unbox(*opaque)
    return d.setdefault(key, default)


@njit
def _iter(opaque):
    d = dictobject._unbox(*opaque)
    return list(d.keys())


class NBDict(MutableMapping):
    @classmethod
    def empty(cls, key_type, value_type):
        """
        """
        dcttype = DictType(key_type, value_type)
        return cls(dcttype=dcttype)

    def __init__(self, **kwargs):
        """
        Parameters
        ----------
        dcttype : numba.types.DictType; keyword-only
            The dictionary type
        """
        if len(kwargs) != 1:
            raise TypeError("too many keyword parameters")
        dcttype = kwargs['dcttype']
        if not isinstance(dcttype, DictType):
            raise TypeError('*dcttype* must be a DictType')
        self._dict_type = dcttype
        ptr = _make_dict(
            self._dict_type.key_type,
            self._dict_type.value_type,
        )
        self._opaque = (ptr, self._dict_type)

    def __getitem__(self, key):
        return _getitem(self._opaque, key)

    def __setitem__(self, key, value):
        return _setitem(self._opaque, key, value)

    def __delitem__(self, key):
        _delitem(self._opaque, key)

    def __iter__(self):
        return iter(_iter(self._opaque))

    def __len__(self):
        return _length((self._opaque))

    def __contains__(self, key):
        return _contains(self._opaque, key)

    def get(self, key, default=None):
        return _get(self._opaque, key, default)

    def setdefault(self, key, default=None):
        return _setdefault(self._opaque, key, default)
