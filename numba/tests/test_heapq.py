from __future__ import print_function, absolute_import, division

import heapq as hq
import itertools

import numpy as np

from numba import jit
from numba.compiler import Flags
from .support import TestCase, CompilationCache, MemoryLeakMixin

no_pyobj_flags = Flags()
no_pyobj_flags.set("nrt")


def heapify(x):
    return hq.heapify(x)


def heappop(heap):
    return hq.heappop(heap)


def heappush(heap, item):
    return hq.heappush(heap, item)


def heappushpop(heap, item):
    return hq.heappushpop(heap, item)


def heapreplace(heap, item):
    return hq.heapreplace(heap, item)


def nsmallest(n, iterable):
    return hq.nsmallest(n, iterable)


def nlargest(n, iterable):
    return hq.nlargest(n, iterable)


class TestHeapq(MemoryLeakMixin, TestCase):

    def setUp(self):
        super(TestHeapq, self).setUp()
        self.ccache = CompilationCache()
        self.rnd = np.random.RandomState(42)

    def test_heapify_basic_sanity(self):
        pyfunc = heapify
        cfunc = jit(nopython=True)(pyfunc)

        a = [1, 3, 5, 7, 9, 2, 4, 6, 8, 0]
        b = a[:]

        pyfunc(a)
        cfunc(b)
        self.assertPreciseEqual(a, b)

        # includes non-finite elements
        element_pool = [3.142, -10.0, 5.5, np.nan, -np.inf, np.inf]

        # list which may contain duplicate elements
        for x in itertools.combinations_with_replacement(element_pool, 6):
            a = list(x)
            b = a[:]

            pyfunc(a)
            cfunc(b)
            self.assertPreciseEqual(a, b)

        # single element list
        for i in range(len(element_pool)):
            a = [element_pool[i]]
            b = a[:]

            pyfunc(a)
            cfunc(b)
            self.assertPreciseEqual(a, b)

        # elements are tuples
        a = [(3, 33), (1, 11), (2, 22)]
        b = a[:]
        pyfunc(a)
        cfunc(b)
        self.assertPreciseEqual(a, b)

    def check_invariant(self, heap):
        for pos, item in enumerate(heap):
            if pos:
                parentpos = (pos - 1) >> 1
                self.assertTrue(heap[parentpos] <= item)

    def test_push_pop(self):
        # inspired by
        # https://github.com/python/cpython/blob/e42b7051/Lib/test/test_heapq.py
        pyfunc_heappush = heappush
        cfunc_heappush = jit(nopython=True)(pyfunc_heappush)

        pyfunc_heappop = heappop
        cfunc_heappop = jit(nopython=True)(pyfunc_heappop)

        heap = [-1.0]
        data = [-1.0]
        self.check_invariant(heap)
        for i in range(256):
            item = self.rnd.randn(1).item(0)
            data.append(item)
            cfunc_heappush(heap, item)
            self.check_invariant(heap)
        results = []
        while heap:
            item = cfunc_heappop(heap)
            self.check_invariant(heap)
            results.append(item)
        data_sorted = data[:]
        data_sorted.sort()
        self.assertPreciseEqual(data_sorted, results)
        self.check_invariant(results)

    def test_heapify(self):
        # inspired by
        # https://github.com/python/cpython/blob/e42b7051/Lib/test/test_heapq.py
        pyfunc = heapify
        cfunc = jit(nopython=True)(pyfunc)

        for size in list(range(1, 30)) + [20000]:
            heap = self.rnd.random_sample(size).tolist()
            cfunc(heap)
            self.check_invariant(heap)

    def test_heapify_exceptions(self):
        pyfunc = heapify
        cfunc = jit(nopython=True)(pyfunc)

        # Exceptions leak references
        self.disable_leak_check()

        with self.assertTypingError() as e:
            cfunc((1, 5, 4))

        msg = 'heap argument must be a list'
        self.assertIn(msg, str(e.exception))

        with self.assertTypingError() as e:
            cfunc([1 + 1j, 2 - 3j])

        msg = ("'<' not supported between instances "
               "of 'complex' and 'complex'")
        self.assertIn(msg, str(e.exception))

    def test_heappop_basic_sanity(self):
        pyfunc = heappop
        cfunc = jit(nopython=True)(pyfunc)

        def a_variations():
            yield [1, 3, 5, 7, 9, 2, 4, 6, 8, 0]
            yield [(3, 33), (1, 111), (2, 2222)]
            yield np.full(5, fill_value=np.nan).tolist()
            yield np.linspace(-10, -5, 100).tolist()

        for a in a_variations():
            heapify(a)
            b = a[:]

            for i in range(len(a)):
                val_py = pyfunc(a)
                val_c = cfunc(b)
                self.assertPreciseEqual(a, b)
                self.assertPreciseEqual(val_py, val_c)

    def iterables(self):
        yield [1, 3, 5, 7, 9, 2, 4, 6, 8, 0]
        a = np.linspace(-10, 2, 23)
        yield a.tolist()
        yield a[::-1].tolist()
        self.rnd.shuffle(a)
        yield a.tolist()

    def test_heappush_basic(self):
        pyfunc_push = heappush
        cfunc_push = jit(nopython=True)(pyfunc_push)

        pyfunc_pop = heappop
        cfunc_pop = jit(nopython=True)(pyfunc_pop)

        for iterable in self.iterables():
            expected = sorted(iterable)
            heap = [iterable.pop(0)]  # must initialise heap

            for value in iterable:
                cfunc_push(heap, value)

            got = [cfunc_pop(heap) for _ in range(len(heap))]
            self.assertPreciseEqual(expected, got)

    def test_nsmallest_basic(self):
        pyfunc = nsmallest
        cfunc = jit(nopython=True)(pyfunc)

        for iterable in self.iterables():
            for n in range(-5, len(iterable) + 3):
                expected = pyfunc(1, iterable)
                got = cfunc(1, iterable)
                self.assertPreciseEqual(expected, got)

    def test_nlargest_basic(self):
        pyfunc = nlargest
        cfunc = jit(nopython=True)(pyfunc)

        for iterable in self.iterables():
            for n in range(-5, len(iterable) + 3):
                expected = pyfunc(1, iterable)
                got = cfunc(1, iterable)
                self.assertPreciseEqual(expected, got)

    def test_heapreplace_basic(self):
        pyfunc = heapreplace
        cfunc = jit(nopython=True)(pyfunc)

        a = [1, 3, 5, 7, 9, 2, 4, 6, 8, 0]

        heapify(a)
        b = a[:]

        for item in [-4, 4, 14]:
            pyfunc(a, item)
            cfunc(b, item)
            self.assertPreciseEqual(a, b)

        a = np.linspace(-3, 13, 20)
        a[4] = np.nan
        a[-1] = np.inf
        a = a.tolist()

        heapify(a)
        b = a[:]

        for item in [-4.0, 3.142, -np.inf, np.inf]:
            pyfunc(a, item)
            cfunc(b, item)
            self.assertPreciseEqual(a, b)

    def heapiter(self, heap):
        try:
            while 1:
                yield heappop(heap)
        except IndexError:
            pass

    def test_nbest(self):
        # inspired by
        # https://github.com/python/cpython/blob/e42b7051/Lib/test/test_heapq.py
        pyfunc_heapify = heapify
        cfunc_heapify = jit(nopython=True)(pyfunc_heapify)

        pyfunc_heapreplace = heapreplace
        cfunc_heapreplace = jit(nopython=True)(pyfunc_heapreplace)

        data = self.rnd.choice(range(2000), 1000).tolist()
        heap = data[:10]
        cfunc_heapify(heap)

        for item in data[10:]:
            if item > heap[0]:
                cfunc_heapreplace(heap, item)

        self.assertPreciseEqual(list(self.heapiter(heap)), sorted(data)[-10:])

    def test_heapsort(self):
        # inspired by
        # https://github.com/python/cpython/blob/e42b7051/Lib/test/test_heapq.py
        pyfunc_heapify = heapify
        cfunc_heapify = jit(nopython=True)(pyfunc_heapify)

        pyfunc_heappush = heappush
        cfunc_heappush = jit(nopython=True)(pyfunc_heappush)

        pyfunc_heappop = heappop
        cfunc_heappop = jit(nopython=True)(pyfunc_heappop)

        for trial in range(100):
            data = self.rnd.choice(range(5), 10).tolist()
            if trial & 1:
                heap = data[:]
                cfunc_heapify(heap)
            else:
                heap = [data[0]]
                for item in data[1:]:
                    cfunc_heappush(heap, item)
            heap_sorted = [cfunc_heappop(heap) for _ in range(10)]
            self.assertPreciseEqual(heap_sorted, sorted(data))

    def test_nsmallest(self):
        # inspired by
        # https://github.com/python/cpython/blob/e42b7051/Lib/test/test_heapq.py
        pyfunc = nsmallest
        cfunc = jit(nopython=True)(pyfunc)

        data = self.rnd.choice(range(2000), 1000).tolist()

        for n in (0, 1, 2, 10, 100, 400, 999, 1000, 1100):
            self.assertPreciseEqual(list(cfunc(n, data)), sorted(data)[:n])

    def test_nlargest(self):
        # inspired by
        # https://github.com/python/cpython/blob/e42b7051/Lib/test/test_heapq.py
        pyfunc = nlargest
        cfunc = jit(nopython=True)(pyfunc)

        data = self.rnd.choice(range(2000), 1000).tolist()

        for n in (0, 1, 2, 10, 100, 400, 999, 1000, 1100):
            self.assertPreciseEqual(list(cfunc(n, data)),
                                    sorted(data, reverse=True)[:n])

    def test_nbest_with_pushpop(self):
        # inspired by
        # https://github.com/python/cpython/blob/e42b7051/Lib/test/test_heapq.py
        pyfunc_heappushpop = heappushpop
        cfunc_heappushpop = jit(nopython=True)(pyfunc_heappushpop)

        pyfunc_heapify = heapify
        cfunc_heapify = jit(nopython=True)(pyfunc_heapify)

        data = self.rnd.choice(range(2000), 1000).tolist()
        heap = data[:10]
        cfunc_heapify(heap)

        for item in data[10:]:
            cfunc_heappushpop(heap, item)

        self.assertPreciseEqual(list(self.heapiter(heap)), sorted(data)[-10:])

    def test_heappushpop(self):
        # inspired by
        # https://github.com/python/cpython/blob/e42b7051/Lib/test/test_heapq.py
        pyfunc = heappushpop
        cfunc = jit(nopython=True)(pyfunc)

        h = [10]
        x = cfunc(h, 10.0)
        self.assertPreciseEqual((h, x), ([10], 10.0))
        self.assertPreciseEqual(type(h[0]), int)
        self.assertPreciseEqual(type(x), float)

        h = [10]
        x = cfunc(h, 9)
        self.assertPreciseEqual((h, x), ([10], 9))

        h = [10]
        x = cfunc(h, 11)
        self.assertPreciseEqual((h, x), ([11], 10))
