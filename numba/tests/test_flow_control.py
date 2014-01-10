from __future__ import print_function
import unittest
from numba.compiler import compile_isolated, Flags
from numba import types, utils
from numba.tests import usecases
import itertools

enable_pyobj_flags = Flags()
enable_pyobj_flags.set("enable_pyobject")

force_pyobj_flags = Flags()
force_pyobj_flags.set("force_pyobject")


def for_loop_usecase1(x, y):
    result = 0
    for i in range(x):
        result += i
    return result

def for_loop_usecase2(x, y):
    result = 0
    for i, j in enumerate(range(x, y, -1)):
        result += i * j
    return result

def for_loop_usecase3(x, y):
    result = 0
    for i in [x,y]:
        result += i
    return result

def for_loop_usecase4(x, y):
    result = 0
    for i in range(10):
        for j in range(10):
            result += 1
    return result

def for_loop_usecase5(x, y):
    result = 0
    for i in range(x):
        result += 1
        if result > y:
            break
    return result

def for_loop_usecase6(x, y):
    result = 0
    for i in range(x):
        if i > y:
            continue
        result += 1
    return result

def while_loop_usecase1(x, y):
    result = 0
    i = 0
    while i < x:
        result += i
        i += 1
    return result

def while_loop_usecase2(x, y):
    result = 0
    while result != x:
        result += 1
    return result

def while_loop_usecase3(x, y):
    result = 0
    i = 0
    j = 0
    while i < x:
        while j < y:
            result += i + j
            i += 1
            j += 1
    return result

def while_loop_usecase4(x, y):
    result = 0
    while True:
        result += 1
        if result > x:
            break
    return result

def while_loop_usecase5(x, y):
    result = 0
    while result < x:
        if result > y:
            result += 2
            continue
        result += 1
    return result

def ifelse_usecase1(x, y):
    if x > 0:
        pass
    elif y > 0:
        pass
    else:
        pass
    return True

def ifelse_usecase2(x, y):
    if x > y:
        return 1
    elif x == 0 or y == 0:
        return 2
    else:
        return 3

def ifelse_usecase3(x, y):
    if x > 0:
        if y > 0:
            return 1
        elif y < 0:
            return 1
        else:
            return 0
    elif x < 0:
        return 1
    else:
        return 0

def ifelse_usecase4(x, y):
    if x == y:
        return 1


class TestFlowControl(unittest.TestCase):

    def run_test(self, pyfunc, x_operands, y_operands):
        cr = compile_isolated(pyfunc, (types.int32, types.int32))
        cfunc = cr.entry_point
        for x, y in itertools.product(x_operands, y_operands):
            self.assertEqual(cfunc(x, y), pyfunc(x, y))

    def test_for_loop1(self):
        self.run_test(for_loop_usecase1, [-10, 0, 10], [0])

    def test_for_loop2(self):
        self.run_test(for_loop_usecase2, [-10, 0, 10], [-10, 0, 10])

    def test_for_loop3(self):
        self.run_test(for_loop_usecase3, [1], [2])

    def test_for_loop4(self):
        self.run_test(for_loop_usecase4, [10], [10])

    def test_for_loop5(self):
        self.run_test(for_loop_usecase5, [100], [50])

    def test_for_loop6(self):
        self.run_test(for_loop_usecase6, [100], [50])

    def test_while_loop1(self):
        self.run_test(while_loop_usecase1, [10], [0])

    def test_while_loop2(self):
        self.run_test(while_loop_usecase2, [10], [0])

    def test_while_loop3(self):
        self.run_test(while_loop_usecase3, [10], [10])

    def test_while_loop4(self):
        self.run_test(while_loop_usecase4, [10], [0])

    def test_while_loop5(self):
        self.run_test(while_loop_usecase5, [0, 5, 10], [0, 5, 10])

    def test_ifelse1(self):
        self.run_test(ifelse_usecase1, [-1, 0, 1], [-1, 0, 1])

    def test_ifelse2(self):
        self.run_test(ifelse_usecase2, [-1, 0, 1], [-1, 0, 1])

    def test_ifelse3(self):
        self.run_test(ifelse_usecase3, [-1, 0, 1], [-1, 0, 1])

    def test_ifelse4(self):
        self.run_test(ifelse_usecase4, [-1, 0, 1], [-1, 0, 1])

if __name__ == '__main__':
    unittest.main()

