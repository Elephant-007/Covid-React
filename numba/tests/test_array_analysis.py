from __future__ import division

import itertools

import numpy as np

from numba import unittest_support as unittest
from numba import njit, typeof, types, typing, typeof, ir, utils, bytecode
from .support import TestCase, tag
from numba.array_analysis import EquivSet, ArrayAnalysis
from numba.compiler import Pipeline, Flags, _PipelineManager
from numba.targets import cpu
from numba.numpy_support import version as numpy_version

class TestEquivSet(TestCase):
    """
    Test array_analysis.EquivSet.
    """
    @tag('important')
    def test_insert_equiv(self):
        s1 = EquivSet()
        s1.insert_equiv('a', 'b')
        self.assertTrue(s1.is_equiv('a', 'b'))
        self.assertTrue(s1.is_equiv('b', 'a'))
        s1.insert_equiv('c', 'd')
        self.assertTrue(s1.is_equiv('c', 'd'))
        self.assertFalse(s1.is_equiv('c', 'a'))
        s1.insert_equiv('a', 'c')
        self.assertTrue(s1.is_equiv('a', 'b', 'c', 'd'))
        self.assertFalse(s1.is_equiv('a', 'e'))

    @tag('important')
    def test__intersect(self):
        s1 = EquivSet()
        s2 = EquivSet()
        r = s1.intersect(s2)
        self.assertTrue(r.is_empty())
        s1.insert_equiv('a', 'b')
        r = s1.intersect(s2)
        self.assertTrue(r.is_empty())
        s2.insert_equiv('b', 'c')
        r = s1.intersect(s2)
        self.assertTrue(r.is_empty())
        s2.insert_equiv('d', 'a')
        r = s1.intersect(s2)
        self.assertTrue(r.is_empty())
        s1.insert_equiv('a', 'e')
        s2.insert_equiv('c', 'd')
        r = s1.intersect(s2)
        self.assertTrue(r.is_equiv('a', 'b'))
        self.assertFalse(r.is_equiv('a', 'e'))
        self.assertFalse(r.is_equiv('c', 'd'))


class ArrayAnalysisTester(Pipeline):
    @classmethod
    def mk_pipeline(cls, args, return_type=None, flags=None, locals={},
                    library=None, typing_context=None, target_context=None):
        if not flags:
            flags = Flags()
        flags.nrt = True
        if typing_context is None:
            typing_context = typing.Context()
        if target_context is None:
            target_context = cpu.CPUContext(typing_context)
        return cls(typing_context, target_context, library, args, return_type,
                   flags, locals)

    def compile_to_ir(self, func):
        """
        Populate and run compiler pipeline
        """
        self.func_id = bytecode.FunctionIdentity.from_function(func)

        try:
            bc = self.extract_bytecode(self.func_id)
        except BaseException as e:
            print("compile_to_ir got error ", e)
            raise e

        self.bc = bc
        self.lifted = ()
        self.lifted_from = None

        pm = _PipelineManager()

        pm.create_pipeline("nopython")
        if self.func_ir is None:
            pm.add_stage(self.stage_analyze_bytecode, "analyzing bytecode")
        pm.add_stage(self.stage_process_ir, "processing IR")
        if not self.flags.no_rewrites:
            if self.status.can_fallback:
                pm.add_stage(self.stage_preserve_ir, "preserve IR for fallback")
            pm.add_stage(self.stage_generic_rewrites, "nopython rewrites")
        pm.add_stage(self.stage_inline_pass, "inline calls to locally defined closures")
        pm.add_stage(self.stage_nopython_frontend, "nopython frontend")
        pm.add_stage(self.stage_annotate_type, "annotate type")
        if not self.flags.no_rewrites:
            pm.add_stage(self.stage_nopython_rewrites, "nopython rewrites")
        def stage_array_analysis():
            self.array_analysis = ArrayAnalysis(self.typingctx, self.func_ir,
                                                self.type_annotation.typemap,
                                                self.type_annotation.calltypes)
            self.array_analysis.run()
        pm.add_stage(stage_array_analysis, "analyze array equivalences")
        pm.finalize()
        res = pm.run(self.status)
        return self.array_analysis

class TestArrayAnalysis(TestCase):

    def run_and_compare(self, fn, args):
        a = fn(*args)
        b = njit(parallel=True)(fn)(*args)
        self.assertEqual(a, b)

    def _compile_and_test(self, fn, arg_tys, asserts=[], equivs=[]):
        """
        Compile the given function and get its IR.
        """
        test_pipeline = ArrayAnalysisTester.mk_pipeline(arg_tys)
        analysis = test_pipeline.compile_to_ir(fn)
        if equivs:
            for func in equivs:
                # only test the equiv_set of the first block
                func(analysis.equiv_sets[0])
        if asserts == None:
            self.assertTrue(self._has_no_assertcall(analysis.func_ir))
        else:
            for func in asserts:
                func(analysis.func_ir, analysis.equiv_sets)

    def _match_argname(self, equiv_set, name, args):
        print("match argname ", name, args)
        shape = equiv_set.get_shape(name) if equiv_set.has_shape(name) else None
        for arg in args:
            if arg == name or arg.startswith():
                return True
        return False

    def _has_assertcall(self, func_ir, equiv_sets, args):
        for label, block in func_ir.blocks.items():
            equiv_set = equiv_sets[label]
            shapes = None
            if all([equiv_set.has_shape(x) for x in args]):
                # NOTE: we only check first dimension of shapes
                shapes = [equiv_set.get_shape(x)[0].name for x in args]
                if len(set(shapes)) <= 1: # all equivalent, no assertion
                    continue
            for expr in block.find_exprs(op='call'):
                fn = func_ir.get_definition(expr.func.name)
                if isinstance(fn, ir.Global) and fn.name == 'assert_equiv':
                    args_names = tuple(x.name for x in expr.args)
                    if (all([x in args_names for x in args]) or
                        (shapes and all([x in args_names for x in shapes]))):
                        return True
        return False

    def _has_shapecall(self, func_ir, x):
        for label, block in func_ir.blocks.items():
            for expr in block.find_exprs(op='getattr'):
                if expr.attr == 'shape':
                    y = func_ir.get_definition(expr.value, lhs_only=True)
                    z = func_ir.get_definition(x, lhs_only=True)
                    y = y.name if isinstance(y, ir.Var) else y
                    z = z.name if isinstance(z, ir.Var) else z
                    if y == z:
                        return True
        return False

    def _has_no_assertcall(self, func_ir):
        for label, block in func_ir.blocks.items():
            for expr in block.find_exprs(op='call'):
                fn = func_ir.get_definition(expr.func.name)
                if isinstance(fn, ir.Global) and fn.name == 'assert_equiv':
                    return False
        return True

    def with_assert(self, *args):
        return lambda func_ir, equiv_set: self.assertTrue(
                        self._has_assertcall(func_ir, equiv_set, args))

    def without_assert(self, *args):
        return lambda func_ir, equiv_set: self.assertFalse(
                        self._has_assertcall(func_ir, equiv_set, args))

    def with_equiv(self, *args):
        def check(equiv_set):
            n = len(args)
            for i in range(n-1):
                if not equiv_set.is_equiv(args[i], args[n-1]):
                    return False
            return True
        return lambda equiv_set: self.assertTrue(check(equiv_set))

    def without_equiv(self, *args):
        def check(equiv_set):
            n = len(args)
            for i in range(n-1):
                if equiv_set.is_equiv(args[i], args[n-1]):
                    return False
            return True
        return lambda equiv_set: self.assertTrue(check(equiv_set))

    def with_shapecall(self, x):
        return lambda func_ir, s: self.assertTrue(self._has_shapecall(func_ir, x))

    def without_shapecall(self, x):
        return lambda func_ir, s: self.assertFalse(self._has_shapecall(func_ir, x))

    def test_base_cases(self):
        def test_0():
            a = np.zeros(0)
            b = np.zeros(1)
            m = 0
            n = 1
            c = np.zeros((m,n))
            return
        self._compile_and_test(test_0, (),
                               equivs = [ self.with_equiv('a',(0,)),
                                          self.with_equiv('b',(1,)),
                                          self.with_equiv('c',(0, 1)) ])

        def test_1(n):
            a = np.zeros(n)
            b = np.zeros(n)
            return a + b
        self._compile_and_test(test_1, (types.intp,), asserts = None)

        def test_2(m, n):
            a = np.zeros(n)
            b = np.zeros(m)
            return a + b
        self._compile_and_test(test_2, (types.intp, types.intp),
                               asserts = [ self.with_assert('a', 'b') ])

        def test_3(n):
            a = np.zeros(n)
            return a + n
        self._compile_and_test(test_3, (types.intp,), asserts = None)

        def test_4(n):
            a = np.zeros(n)
            b = a + 1
            c = a + 2
            return a + c
        self._compile_and_test(test_4, (types.intp,), asserts = None)

        def test_5(n):
            a = np.zeros((n,n))
            m = n
            b = np.zeros((m,n))
            return a + b
        self._compile_and_test(test_5, (types.intp,), asserts = None)

        def test_6(m, n):
            a = np.zeros(n)
            b = np.zeros(m)
            d = a + b
            e = a - b
            return d + e
        self._compile_and_test(test_6, (types.intp, types.intp),
                               asserts = [ self.with_assert('a', 'b'),
                                           self.without_assert('d', 'e') ])

        def test_7(m, n):
            a = np.zeros(n)
            b = np.zeros(m)
            if m == 10:
                d = a + b
            else:
                d = a - b
            return d + a
        self._compile_and_test(test_7, (types.intp, types.intp),
                               asserts = [ self.with_assert('a', 'b'),
                                           self.without_assert('d', 'a') ])

        def test_8(m, n):
            a = np.zeros(n)
            b = np.zeros(m)
            if m == 10:
                d = b + a
            else:
                d = a + a
            return b + d
        self._compile_and_test(test_8, (types.intp, types.intp),
                               asserts = [ self.with_assert('a', 'b'),
                                           self.with_assert('b', 'd') ])

        def test_9(m, n):
            A = np.ones(m)
            B = np.ones(n)
            return np.sum(A + B)

        self.run_and_compare(test_9, (10,10))
        with self.assertRaises(AssertionError) as raises:
            cfunc = njit(parallel=True)(test_9)
            cfunc(10, 9)
        msg = "Sizes of A, B do not match"
        self.assertIn(msg, str(raises.exception))

        def test_shape(A):
            (m,n) = A.shape
            B = np.ones((m,n))
            return A + B
        self._compile_and_test(test_shape, (types.Array(types.intp, 2, 'C'),),
                               asserts = None)

        def test_cond(l, m, n):
            A = np.ones(l)
            B = np.ones(m)
            C = np.ones(n)
            if l == m:
               r = np.sum(A + B)
            else:
               r = 0
            if m != n:
               s = 0
            else:
               s = np.sum(B + C)
            t = 0
            if l == m:
                if m == n:
                    t = np.sum(A + B + C)
            return r + s + t
        self._compile_and_test(test_cond, (types.intp, types.intp, types.intp),
                               asserts = None)

        def test_assert(m, n):
            assert(m == n)
            A = np.ones(m)
            B = np.ones(n)
            return np.sum(A + B)
        self._compile_and_test(test_assert, (types.intp, types.intp),
                               asserts = None)

    def test_numpy_calls(self):
        def test_zeros(n):
            a = np.zeros(n)
            b = np.zeros((n, n))
            c = np.zeros(shape=(n, n))
        self._compile_and_test(test_zeros, (types.intp,),
                               equivs = [ self.with_equiv('a', 'n'),
                                          self.with_equiv('b', ('n', 'n')),
                                          self.with_equiv('b', 'c') ])

        def test_ones(n):
            a = np.ones(n)
            b = np.ones((n, n))
            c = np.ones(shape=(n, n))
        self._compile_and_test(test_ones, (types.intp,),
                               equivs = [ self.with_equiv('a', 'n'),
                                          self.with_equiv('b', ('n', 'n')),
                                          self.with_equiv('b', 'c') ])

        def test_empty(n):
            a = np.empty(n)
            b = np.empty((n, n))
            c = np.empty(shape=(n, n))
        self._compile_and_test(test_empty, (types.intp,),
                               equivs = [ self.with_equiv('a', 'n'),
                                          self.with_equiv('b', ('n', 'n')),
                                          self.with_equiv('b', 'c') ])

        def test_eye(n):
            a = np.eye(n)
            b = np.eye(N=n)
            c = np.eye(N=n, M=n)
            d = np.eye(N=n, M=n+1)
        self._compile_and_test(test_eye, (types.intp,),
                               equivs = [ self.with_equiv('a', ('n', 'n')),
                                          self.with_equiv('b', ('n', 'n')),
                                          self.with_equiv('b', 'c'),
                                          self.without_equiv('b', 'd') ])

        def test_identity(n):
            a = np.identity(n)
        self._compile_and_test(test_identity, (types.intp,),
                               equivs = [ self.with_equiv('a', ('n', 'n')) ])


        def test_diag(n):
            a = np.identity(n)
            b = np.diag(a)
            c = np.diag(b)
            d = np.diag(a, k=1)
        self._compile_and_test(test_diag, (types.intp,),
                               equivs = [ self.with_equiv('b', ('n',)),
                                          self.with_equiv('c', ('n', 'n')) ],
                               asserts = [ self.with_shapecall('d'),
                                           self.without_shapecall('c') ])

        def test_array_like(a):
            b = np.empty_like(a)
            c = np.zeros_like(a)
            d = np.ones_like(a)
            e = np.full_like(a, 1)
            f = np.asfortranarray(a)

        self._compile_and_test(test_array_like, (types.Array(types.intp, 2, 'C'),),
                               equivs = [ self.with_equiv('a', 'b', 'd', 'e', 'f') ],
                               asserts = [ self.with_shapecall('a'),
                                           self.without_shapecall('b') ])

        def test_reshape(n):
            a = np.ones(n * n)
            b = a.reshape((n, n))
            return a.sum() + b.sum()
        self._compile_and_test(test_reshape, (types.intp,),
                               equivs = [ self.with_equiv('b', ('n', 'n')) ],
                               asserts = [ self.without_shapecall('b') ])

        def test_transpose(m, n):
            a = np.ones((m, n))
            b = a.T
            # Numba njit cannot compile explicit transpose call!
            # c = np.transpose(b)
        self._compile_and_test(test_transpose, (types.intp, types.intp),
                               equivs = [ self.with_equiv('a', ('m','n')),
                                          self.with_equiv('b', ('n','m')) ])

        def test_random(n):
            a0 = np.random.rand(n)
            a1 = np.random.rand(n, n)
            b0 = np.random.randn(n)
            b1 = np.random.randn(n, n)
            c0 = np.random.ranf(n)
            c1 = np.random.ranf((n, n))
            c2 = np.random.ranf(size=(n, n))
            d0 = np.random.random_sample(n)
            d1 = np.random.random_sample((n, n))
            d2 = np.random.random_sample(size=(n, n))
            e0 = np.random.sample(n)
            e1 = np.random.sample((n, n))
            e2 = np.random.sample(size=(n, n))
            f0 = np.random.random(n)
            f1 = np.random.random((n, n))
            f2 = np.random.random(size=(n, n))
            g0 = np.random.standard_normal(n)
            g1 = np.random.standard_normal((n, n))
            g2 = np.random.standard_normal(size=(n, n))
            h0 = np.random.chisquare(10,n)
            h1 = np.random.chisquare(10,(n,n))
            h2 = np.random.chisquare(10,size=(n,n))
            i0 = np.random.weibull(10,n)
            i1 = np.random.weibull(10,(n,n))
            i2 = np.random.weibull(10,size=(n,n))
            j0 = np.random.power(10,n)
            j1 = np.random.power(10,(n,n))
            j2 = np.random.power(10,size=(n,n))
            k0 = np.random.geometric(0.1,n)
            k1 = np.random.geometric(0.1,(n,n))
            k2 = np.random.geometric(0.1,size=(n,n))
            l0 = np.random.exponential(10,n)
            l1 = np.random.exponential(10,(n,n))
            l2 = np.random.exponential(10,size=(n,n))
            m0 = np.random.poisson(10,n)
            m1 = np.random.poisson(10,(n,n))
            m2 = np.random.poisson(10,size=(n,n))
            n0 = np.random.rayleigh(10,n)
            n1 = np.random.rayleigh(10,(n,n))
            n2 = np.random.rayleigh(10,size=(n,n))
            o0 = np.random.normal(0,1,n)
            o1 = np.random.normal(0,1,(n,n))
            o2 = np.random.normal(0,1,size=(n,n))
            p0 = np.random.uniform(0,1,n)
            p1 = np.random.uniform(0,1,(n,n))
            p2 = np.random.uniform(0,1,size=(n,n))
            q0 = np.random.beta(0.1,1,n)
            q1 = np.random.beta(0.1,1,(n,n))
            q2 = np.random.beta(0.1,1,size=(n,n))
            r0 = np.random.binomial(0,1,n)
            r1 = np.random.binomial(0,1,(n,n))
            r2 = np.random.binomial(0,1,size=(n,n))
            s0 = np.random.f(0.1,1,n)
            s1 = np.random.f(0.1,1,(n,n))
            s2 = np.random.f(0.1,1,size=(n,n))
            t0 = np.random.gamma(0.1,1,n)
            t1 = np.random.gamma(0.1,1,(n,n))
            t2 = np.random.gamma(0.1,1,size=(n,n))
            u0 = np.random.lognormal(0,1,n)
            u1 = np.random.lognormal(0,1,(n,n))
            u2 = np.random.lognormal(0,1,size=(n,n))
            v0 = np.random.laplace(0,1,n)
            v1 = np.random.laplace(0,1,(n,n))
            v2 = np.random.laplace(0,1,size=(n,n))
            w0 = np.random.randint(0,10,n)
            w1 = np.random.randint(0,10,(n,n))
            w2 = np.random.randint(0,10,size=(n,n))
            x0 = np.random.triangular(-3,0,10,n)
            x1 = np.random.triangular(-3,0,10,(n,n))
            x2 = np.random.triangular(-3,0,10,size=(n,n))


        last=ord('x')+1
        vars1d = [('n',)] + [chr(x)+'0' for x in range(ord('a'),last)]
        vars2d = [('n', 'n')] + [chr(x)+'1' for x in range(ord('a'),last)]
        vars2d += [chr(x)+'1' for x in range(ord('c'),last)]
        self._compile_and_test(test_random, (types.intp,),
                               equivs = [ self.with_equiv(*vars1d),
                                          self.with_equiv(*vars2d) ])

        def test_concatenate(m, n):
            a = np.ones(m)
            b = np.ones(n)
            c = np.concatenate((a,b))
            d = np.ones((2,n))
            e = np.ones((3,n))
            f = np.concatenate((d, e))
            # Numba njit cannot compile concatenate with single array!
            # g = np.ones((3,4,5))
            # h = np.concatenate(g)
            i = np.ones((m, 2))
            j = np.ones((m, 3))
            k = np.concatenate((i, j), axis=1)
            l = np.ones((m, n))
            o = np.ones((m, n))
            p = np.concatenate((l, o))
            # Numba njit cannot support list argument!
            # q = np.concatenate([d, e])
        self._compile_and_test(test_concatenate, (types.intp, types.intp),
                               equivs = [ self.with_equiv('f', (5, 'n')),
                                          #self.with_equiv('h', (3 + 4 + 5, )),
                                          self.with_equiv('k', ('m', 5)) ],
                               asserts = [ self.with_shapecall('c'),
                                           self.without_shapecall('f'),
                                           self.without_shapecall('k'),
                                           self.with_shapecall('p') ])

        def test_vsd_stack():
            k = np.ones((2,))
            l = np.ones((2,3))
            o = np.ones((2,3,4))
            p = np.vstack((k, k))
            q = np.vstack((l, l))
            r = np.hstack((k, k))
            s = np.hstack((l, l))
            t = np.dstack((k, k))
            u = np.dstack((l, l))
            v = np.dstack((o, o))

        self._compile_and_test(test_vsd_stack, (),
                               equivs = [ self.with_equiv('p', (2, 2)),
                                          self.with_equiv('q', (4, 3)),
                                          self.with_equiv('r', (4,)),
                                          self.with_equiv('s', (2, 6)),
                                          self.with_equiv('t', (1, 2, 2)),
                                          self.with_equiv('u', (2, 3, 2)),
                                          self.with_equiv('v', (2, 3, 8)),
                                        ])

        if numpy_version >= (1, 10):
            def test_stack(m, n):
                a = np.ones(m)
                b = np.ones(n)
                c = np.stack((a, b))
                d = np.ones((m, n))
                e = np.ones((m, n))
                f = np.stack((d, e))
                g = np.stack((d, e), axis=0)
                h = np.stack((d, e), axis=1)
                i = np.stack((d, e), axis=2)
                j = np.stack((d, e), axis=-1)

            self._compile_and_test(test_stack, (types.intp, types.intp),
                                   equivs = [ self.with_equiv('m', 'n'),
                                              self.with_equiv('c', (2, 'm')),
                                              self.with_equiv('f', 'g', (2, 'm', 'n')),
                                              self.with_equiv('h', ('m', 2, 'n')),
                                              self.with_equiv('i', 'j', ('m', 'n', 2)),
                                            ])

        def test_linspace(m,n):
            a = np.linspace(m,n)
            b = np.linspace(m,n,10)
            # Numba njit does not support num keyword to linspace call!
            # c = np.linspace(m,n,num=10)
        self._compile_and_test(test_linspace, (types.float64,types.float64),
                               equivs = [ self.with_equiv('a', (50,)),
                                          self.with_equiv('b', (10,)) ])

        def test_dot(m,n):
            a = np.dot(np.ones(1),np.ones(1))
            b = np.dot(np.ones(2),np.ones((2,3)))
            # Numba njit does not support higher dimensional inputs
            #c = np.dot(np.ones(2),np.ones((3,2,4)))
            #d = np.dot(np.ones(2),np.ones((3,5,2,4)))
            e = np.dot(np.ones((1,2)),np.ones(2,))
            #f = np.dot(np.ones((1,2,3)),np.ones(3,))
            #g = np.dot(np.ones((1,2,3,4)),np.ones(4,))
            h = np.dot(np.ones((2,3)),np.ones((3,4)))
            i = np.dot(np.ones((m,n)),np.ones((n,m)))
            l = m + n - n
            j = np.dot(np.ones((m,m)),np.ones((l,l)))

            # Numba njit does not support num keyword to linspace call!
            # c = np.linspace(m,n,num=10)
        self._compile_and_test(test_dot, (types.intp,types.intp),
                               equivs = [ self.without_equiv('a', (1,)), # not array
                                          self.with_equiv('b', (3,)),
                                          self.with_equiv('e', (1,)),
                                          self.with_equiv('h', (2,4)),
                                          self.with_equiv('i', ('m','m')),
                                          self.with_equiv('j', ('m','m')),
                                        ],
                               asserts = [ self.with_assert('m', 'l') ])

        def test_broadcast(m,n):
            a = np.ones((m,n))
            b = np.ones(n)
            c = a + b
            d = np.ones((1,n))
            e = a + c - d
        self._compile_and_test(test_broadcast, (types.intp, types.intp),
                               equivs = [ self.with_equiv('a', 'c', 'e') ],
                               asserts = None)


if __name__ == '__main__':
    unittest.main()
