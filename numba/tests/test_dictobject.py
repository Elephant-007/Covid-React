"""
Testing numba implementation of the numba dictionary.

The tests here only check that the numba typing and codegen are working
correctly.  Detailed testing of the underlying dictionary operations is done
in test_dictimpl.py.
"""
from __future__ import print_function, absolute_import, division

import sys
import numpy as np
from numba import njit, utils
from numba import int32, int64, float32, float64, types
from numba import dictobject
from numba import types
from numba.typeddict import TypedDict
from numba.errors import TypingError
from .support import TestCase, MemoryLeakMixin, unittest


class TestDictObject(MemoryLeakMixin, TestCase):
    def test_dict_create(self):
        """
        Exercise dictionary creation, insertion and len
        """
        @njit
        def foo(n):
            d = dictobject.new_dict(int32, float32)
            for i in range(n):
                d[i] = i + 1
            return len(d)

        # Insert nothing
        self.assertEqual(foo(n=0), 0)
        # Insert 1 entry
        self.assertEqual(foo(n=1), 1)
        # Insert 2 entries
        self.assertEqual(foo(n=2), 2)
        # Insert 100 entries
        self.assertEqual(foo(n=100), 100)

    def test_dict_get(self):
        """
        Exercise dictionary creation, insertion and get
        """
        @njit
        def foo(n, targets):
            d = dictobject.new_dict(int32, float64)
            # insertion loop
            for i in range(n):
                d[i] = i
            # retrieval loop
            output = []
            for t in targets:
                output.append(d.get(t))
            return output

        self.assertEqual(foo(5, [0, 1, 9]), [0, 1, None])
        self.assertEqual(foo(10, [0, 1, 9]), [0, 1, 9])
        self.assertEqual(foo(10, [-1, 9, 1]), [None, 9, 1])

    def test_dict_get_with_default(self):
        """
        Exercise dict.get(k, d) where d is set
        """
        @njit
        def foo(n, target, default):
            d = dictobject.new_dict(int32, float64)
            # insertion loop
            for i in range(n):
                d[i] = i
            # retrieval loop
            return d.get(target, default)

        self.assertEqual(foo(5, 3, -1), 3)
        self.assertEqual(foo(5, 5, -1), -1)

    def test_dict_getitem(self):
        """
        Exercise dictionary __getitem__
        """
        @njit
        def foo(keys, vals, target):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v

            # lookup
            return d[target]

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]
        self.assertEqual(foo(keys, vals, 1), 0.1)
        self.assertEqual(foo(keys, vals, 2), 0.2)
        self.assertEqual(foo(keys, vals, 3), 0.3)
        # check no leak so far
        self.assert_no_memory_leak()
        # disable leak check for exception test
        self.disable_leak_check()
        with self.assertRaises(KeyError):
            foo(keys, vals, 0)
        with self.assertRaises(KeyError):
            foo(keys, vals, 4)

    def test_dict_popitem(self):
        """
        Exercise dictionary .popitem
        """
        @njit
        def foo(keys, vals):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v

            # popitem
            return d.popitem()

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]
        for i in range(1, len(keys)):
            self.assertEqual(
                foo(keys[:i], vals[:i]),
                (keys[i - 1], vals[i - 1]),
            )

    def test_dict_popitem_many(self):
        """
        Exercise dictionary .popitem
        """

        @njit
        def core(d, npop):
            # popitem
            keysum, valsum = 0, 0
            for _ in range(npop):
                k, v = d.popitem()
                keysum += k
                valsum -= v
            return keysum, valsum

        @njit
        def foo(keys, vals, npop):
            d = dictobject.new_dict(int32, int32)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v

            return core(d, npop)

        keys = [1, 2, 3]
        vals = [10, 20, 30]

        for i in range(len(keys)):
            self.assertEqual(
                foo(keys, vals, npop=3),
                core.py_func(dict(zip(keys, vals)), npop=3),
            )

        # check no leak so far
        self.assert_no_memory_leak()
        # disable leak check for exception test
        self.disable_leak_check()

        with self.assertRaises(KeyError):
            foo(keys, vals, npop=4)

    def test_dict_pop(self):
        """
        Exercise dictionary .pop
        """
        @njit
        def foo(keys, vals, target):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v

            # popitem
            return d.pop(target, None), len(d)

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]

        self.assertEqual(foo(keys, vals, 1), (0.1, 2))
        self.assertEqual(foo(keys, vals, 2), (0.2, 2))
        self.assertEqual(foo(keys, vals, 3), (0.3, 2))
        self.assertEqual(foo(keys, vals, 0), (None, 3))

        # check no leak so far
        self.assert_no_memory_leak()
        # disable leak check for exception test
        self.disable_leak_check()

        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            # popitem
            return d.pop(0)

        with self.assertRaises(KeyError):
            foo()

    def test_dict_pop_many(self):
        """
        Exercise dictionary .pop
        """

        @njit
        def core(d, pops):
            total = 0
            for k in pops:
                total += k + d.pop(k, 0.123) + len(d)
                total *= 2
            return total

        @njit
        def foo(keys, vals, pops):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v
            # popitem
            return core(d, pops)

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]
        pops = [2, 3, 3, 1, 0, 2, 1, 0, -1]

        self.assertEqual(
            foo(keys, vals, pops),
            core.py_func(dict(zip(keys, vals)), pops),
        )

    def test_dict_delitem(self):
        @njit
        def foo(keys, vals, target):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v
            del d[target]
            return len(d), d.get(target)

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]
        self.assertEqual(foo(keys, vals, 1), (2, None))
        self.assertEqual(foo(keys, vals, 2), (2, None))
        self.assertEqual(foo(keys, vals, 3), (2, None))
        # check no leak so far
        self.assert_no_memory_leak()
        # disable leak check for exception test
        self.disable_leak_check()
        with self.assertRaises(KeyError):
            foo(keys, vals, 0)

    def test_dict_clear(self):
        """
        Exercise dict.clear
        """
        @njit
        def foo(keys, vals):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v
            b4 = len(d)
            # clear
            d.clear()
            return b4, len(d)

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]
        self.assertEqual(foo(keys, vals), (3, 0))

    def test_dict_items(self):
        """
        Exercise dict.items
        """
        @njit
        def foo(keys, vals):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v
            out = []
            for kv in d.items():
                out.append(kv)
            return out

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]

        self.assertEqual(
            foo(keys, vals),
            list(zip(keys, vals)),
        )

        # Test .items() on empty dict
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            out = []
            for kv in d.items():
                out.append(kv)
            return out

        self.assertEqual(foo(), [])

    def test_dict_keys(self):
        """
        Exercise dict.keys
        """
        @njit
        def foo(keys, vals):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v
            out = []
            for k in d.keys():
                out.append(k)
            return out

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]

        self.assertEqual(
            foo(keys, vals),
            keys,
        )

    def test_dict_values(self):
        """
        Exercise dict.values
        """
        @njit
        def foo(keys, vals):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v
            out = []
            for v in d.values():
                out.append(v)
            return out

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]

        self.assertEqual(
            foo(keys, vals),
            vals,
        )

    def test_dict_iter(self):
        """
        Exercise iter(dict)
        """
        @njit
        def foo(keys, vals):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v
            out = []
            for k in d:
                out.append(k)
            return out

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]

        self.assertEqual(
            foo(keys, vals),
            [1, 2, 3]
        )

    def test_dict_contains(self):
        """
        Exercise operator.contains
        """
        @njit
        def foo(keys, vals, checklist):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v
            out = []
            for k in checklist:
                out.append(k in d)
            return out

        keys = [1, 2, 3]
        vals = [0.1, 0.2, 0.3]

        self.assertEqual(
            foo(keys, vals, [2, 3, 4, 1, 0]),
            [True, True, False, True, False],
        )

    def test_dict_copy(self):
        """
        Exercise dict.copy
        """
        @njit
        def foo(keys, vals):
            d = dictobject.new_dict(int32, float64)
            # insertion
            for k, v in zip(keys, vals):
                d[k] = v
            return list(d.copy().items())

        keys = list(range(20))
        vals = [x + i / 100 for i, x in enumerate(keys)]
        out = foo(keys, vals)
        self.assertEqual(out, list(zip(keys, vals)))

    def test_dict_setdefault(self):
        """
        Exercise dict.setdefault
        """
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            d.setdefault(1, 1.2) # used because key is not in
            a = d.get(1)
            d[1] = 2.3
            b = d.get(1)
            d[2] = 3.4
            d.setdefault(2, 4.5)  # not used because key is in
            c = d.get(2)
            return a, b, c

        self.assertEqual(foo(), (1.2, 2.3, 3.4))

    def test_dict_equality(self):
        """
        Exercise dict.__eq__ and .__ne__
        """
        @njit
        def foo(na, nb, fa, fb):
            da = dictobject.new_dict(int32, float64)
            db = dictobject.new_dict(int32, float64)
            for i in range(na):
                da[i] = i * fa
            for i in range(nb):
                db[i] = i * fb
            return da == db, da != db

        # Same keys and values
        self.assertEqual(foo(10, 10, 3, 3), (True, False))
        # Same keys and diff values
        self.assertEqual(foo(10, 10, 3, 3.1), (False, True))
        # LHS has more keys
        self.assertEqual(foo(11, 10, 3, 3), (False, True))
        # RHS has more keys
        self.assertEqual(foo(10, 11, 3, 3), (False, True))

    def test_dict_equality_more(self):
        """
        Exercise dict.__eq__
        """
        @njit
        def foo(ak, av, bk, bv):
            # The key-value types are different in the two dictionaries
            da = dictobject.new_dict(int32, float64)
            db = dictobject.new_dict(int64, float32)
            for i in range(len(ak)):
                da[ak[i]] = av[i]
            for i in range(len(bk)):
                db[bk[i]] = bv[i]
            return da == db

        # Simple equal case
        ak = [1, 2, 3]
        av = [2, 3, 4]
        bk = [1, 2, 3]
        bv = [2, 3, 4]
        self.assertTrue(foo(ak, av, bk, bv))

        # Equal with replacement
        ak = [1, 2, 3]
        av = [2, 3, 4]
        bk = [1, 2, 2, 3]
        bv = [2, 1, 3, 4]
        self.assertTrue(foo(ak, av, bk, bv))

        # Diff values
        ak = [1, 2, 3]
        av = [2, 3, 4]
        bk = [1, 2, 3]
        bv = [2, 1, 4]
        self.assertFalse(foo(ak, av, bk, bv))

        # Diff keys
        ak = [0, 2, 3]
        av = [2, 3, 4]
        bk = [1, 2, 3]
        bv = [2, 3, 4]
        self.assertFalse(foo(ak, av, bk, bv))

    def test_dict_equality_diff_type(self):
        """
        Exercise dict.__eq__
        """
        @njit
        def foo(na, b):
            da = dictobject.new_dict(int32, float64)
            for i in range(na):
                da[i] = i
            return da == b

        # dict != int
        self.assertFalse(foo(10, 1))
        # dict != tuple[int]
        self.assertFalse(foo(10, (1,)))

    def test_dict_to_from_meminfo(self):
        """
        Exercise dictobject.{_as_meminfo, _from_meminfo}
        """
        @njit
        def make_content(nelem):
            for i in range(nelem):
                yield i, i + (i + 1) / 100

        @njit
        def boxer(nelem):
            d = dictobject.new_dict(int32, float64)
            for k, v in make_content(nelem):
                d[k] = v
            return dictobject._as_meminfo(d)

        dcttype = types.DictType(int32, float64)

        @njit
        def unboxer(mi):
            d = dictobject._from_meminfo(mi, dcttype)
            return list(d.items())

        mi = boxer(10)
        self.assertEqual(mi.refcount, 1)

        got = unboxer(mi)
        expected = list(make_content.py_func(10))
        self.assertEqual(got, expected)

    def test_001_cannot_downcast_key(self):
        @njit
        def foo(n):
            d = dictobject.new_dict(int32, float64)
            for i in range(n):
                d[i] = i + 1
            # bad key type
            z = d.get(1j)
            return z

        with self.assertRaises(TypingError) as raises:
            foo(10)
        self.assertIn(
            'cannot safely cast complex128 to int32',
            str(raises.exception),
        )

    def test_002_cannot_downcast_default(self):
        @njit
        def foo(n):
            d = dictobject.new_dict(int32, float64)
            for i in range(n):
                d[i] = i + 1
            # bad default type
            z = d.get(2 * n, 1j)
            return z

        with self.assertRaises(TypingError) as raises:
            foo(10)
        self.assertIn(
            'cannot safely cast complex128 to float64',
            str(raises.exception),
        )

    def test_003_cannot_downcast_key(self):
        @njit
        def foo(n):
            d = dictobject.new_dict(int32, float64)
            for i in range(n):
                d[i] = i + 1
            # bad cast!?
            z = d.get(2.4)
            return z

        # should raise
        with self.assertRaises(TypingError) as raises:
            foo(10)
        self.assertIn(
            'cannot safely cast float64 to int32',
            str(raises.exception),
        )

    def test_004_cannot_downcast_key(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            # should raise TypingError
            d[1j] = 7.

        with self.assertRaises(TypingError) as raises:
            foo()
        self.assertIn(
            'cannot safely cast complex128 to int32',
            str(raises.exception),
        )

    def test_005_cannot_downcast_value(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            # should raise TypingError
            d[1] = 1j

        with self.assertRaises(TypingError) as raises:
            foo()
        self.assertIn(
            'cannot safely cast complex128 to float64',
            str(raises.exception),
        )

    def test_006_cannot_downcast_key(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            # raise TypingError
            d[11.5]

        with self.assertRaises(TypingError) as raises:
            foo()
        self.assertIn(
            'cannot safely cast float64 to int32',
            str(raises.exception),
        )

    @unittest.skipUnless(utils.IS_PY3 and sys.maxsize > 2 ** 32,
                         "Python 3, 64 bit test only")
    def test_007_collision_checks(self):
        # this checks collisions in real life for 64bit systems
        @njit
        def foo(v1, v2):
            d = dictobject.new_dict(int64, float64)
            c1 = np.uint64(2 ** 61 - 1)
            c2 = np.uint64(0)
            assert hash(c1) == hash(c2)
            d[c1] = v1
            d[c2] = v2
            return (d[c1], d[c2])

        a, b = 10., 20.
        x, y = foo(a, b)
        self.assertEqual(x, a)
        self.assertEqual(y, b)

    def test_008_lifo_popitem(self):
        # check that (keys, vals) are LIFO .popitem()
        @njit
        def foo(n):
            d = dictobject.new_dict(int32, float64)
            for i in range(n):
                d[i] = i + 1
            keys = []
            vals = []
            for i in range(n):
                tmp = d.popitem()
                keys.append(tmp[0])
                vals.append(tmp[1])
            return keys, vals

        z = 10
        gk, gv = foo(z)

        self.assertEqual(gk, [x for x in reversed(range(z))])
        self.assertEqual(gv, [x + 1 for x in reversed(range(z))])

    def test_010_cannot_downcast_default(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            d[0] = 6.
            d[1] = 7.
            # pop'd default must have same type as value
            d.pop(11, 12j)

        with self.assertRaises(TypingError) as raises:
            foo()
        self.assertIn(
            "cannot safely cast complex128 to float64",
            str(raises.exception),
        )

    def test_011_cannot_downcast_key(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            d[0] = 6.
            d[1] = 7.
            # pop'd key must have same type as key
            d.pop(11j)

        with self.assertRaises(TypingError) as raises:
            foo()
        self.assertIn(
            "cannot safely cast complex128 to int32",
            str(raises.exception),
        )

    def test_012_cannot_downcast_key(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            d[0] = 6.
            # invalid key type
            return 1j in d

        with self.assertRaises(TypingError) as raises:
            foo()
        self.assertIn(
            "cannot safely cast complex128 to int32",
            str(raises.exception),
        )

    def test_013_contains_empty_dict(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            # contains on empty dict
            return 1 in d

        self.assertFalse(foo())

    def test_014_not_contains_empty_dict(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            # not contains empty dict
            return 1 not in d

        self.assertTrue(foo())

    def test_015_dict_clear(self):
        @njit
        def foo(n):
            d = dictobject.new_dict(int32, float64)
            for i in range(n):
                d[i] = i + 1
            x = len(d)
            d.clear()
            y = len(d)
            return x, y

        m = 10
        self.assertEqual(foo(m), (m, 0))

    def test_016_cannot_downcast_key(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            # key is wrong type
            d.setdefault(1j, 12.)

        with self.assertRaises(TypingError) as raises:
            foo()
        self.assertIn(
            "cannot safely cast complex128 to int32",
            str(raises.exception),
        )

    def test_017_cannot_downcast_default(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            # default value is wrong type
            d.setdefault(1, 12.j)

        with self.assertRaises(TypingError) as raises:
            foo()
        self.assertIn(
            "cannot safely cast complex128 to float64",
            str(raises.exception),
        )

    def test_018_keys_iter_are_views(self):
        # this is broken somewhere in llvmlite, intent of test is to check if
        # keys behaves like a view or not
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            d[11] = 12.
            k1 = d.keys()
            d[22] = 9.
            k2 = d.keys()
            rk1 = [x for x in k1]
            rk2 = [x for x in k2]
            return rk1, rk2

        a, b = foo()
        self.assertEqual(a, b)
        self.assertEqual(a, [11, 22])

    # Not implemented yet
    @unittest.expectedFailure
    def test_019(self):
        # should keys/vals be set-like?
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            d[11] = 12.
            d[22] = 9.
            k2 = d.keys() & {12,}
            return k2

        print(foo())

    @unittest.skip("refct")
    def test_020(self):
        # this should work ?!
        @njit
        def foo():
            d = dictobject.new_dict(types.unicode_type, float64)
            d['a'] = 1.
            d['b'] = 2.
            d['c'] = 3.
            d['d'] = 4.
            for x in d.items():
                print(x)
            return d['a']

        print(foo())

    @unittest.skip("refct")
    def test_021(self):
        # this should work ?!
        @njit
        def foo():
            d = dictobject.new_dict(types.unicode_type, float64)
            tmp = []
            for i in range(10000):
                tmp.append('a')
            s = ''.join(tmp)
            print(s)
            d[s] = 1.
            # this prints out weirdly, issue may well be print related.
            for x in d.items():
                print(x)

        print(foo())

    def test_022_references_juggle(self):
        # this should work, llvmlite level broken, probably the same problem as
        # before, intent of test is to juggle references about
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)
            e = d
            d[1] = 12.
            e[2] = 14.
            e = dictobject.new_dict(int32, float64)
            e[1] = 100.
            e[2] = 1000.
            f = d
            d = e

            k1 = [x for x in d.items()]
            k2 = [x for x in e.items()]
            k3 = [x for x in f.items()]

            return k1, k2, k3

        k1, k2, k3 = foo()
        self.assertEqual(k1, [(1, 100.0), (2, 1000.0)])
        self.assertEqual(k2, [(1, 100.0), (2, 1000.0)])
        self.assertEqual(k3, [(1, 12), (2, 14)])

    def test_023_closure(self):
        @njit
        def foo():
            d = dictobject.new_dict(int32, float64)

            def bar():
                d[1] = 12.
                d[2] = 14.
            bar()
            return [x for x in d.keys()]

        self.assertEqual(foo(), [1, 2])

class TestTypedDict(MemoryLeakMixin, TestCase):
    def test_basic(self):
        d = TypedDict.empty(int32, float32)
        # len
        self.assertEqual(len(d), 0)
        # setitems
        d[1] = 1
        d[2] = 2.3
        d[3] = 3.4
        self.assertEqual(len(d), 3)
        # keys
        self.assertEqual(list(d.keys()), [1, 2, 3])
        # values
        for x, y in zip(list(d.values()), [1, 2.3, 3.4]):
            self.assertAlmostEqual(x, y, places=4)
        # getitem
        self.assertAlmostEqual(d[1], 1)
        self.assertAlmostEqual(d[2], 2.3, places=4)
        self.assertAlmostEqual(d[3], 3.4, places=4)
        # deltiem
        del d[2]
        self.assertEqual(len(d), 2)
        # get
        self.assertIsNone(d.get(2))
        # setdefault
        d.setdefault(2, 100)
        d.setdefault(3, 200)
        self.assertEqual(d[2], 100)
        self.assertAlmostEqual(d[3], 3.4, places=4)

    def test_copy_from_dict(self):
        expect = {k: float(v) for k, v in zip(range(10), range(10, 20))}
        nbd = TypedDict.empty(int32, float64)
        for k, v in expect.items():
            nbd[k] = v
        got = dict(nbd)
        self.assertEqual(got, expect)

    def test_compiled(self):
        @njit
        def producer():
            d = TypedDict.empty(int32, float64)
            d[1] = 1.23
            return d

        @njit
        def consumer(d):
            return d[1]

        d = producer()
        val = consumer(d)
        self.assertEqual(val, 1.23)
