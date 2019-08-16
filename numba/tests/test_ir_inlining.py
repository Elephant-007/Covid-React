"""
This tests the inline kwarg to @jit and @overload etc, it has nothing to do with
LLVM or low level inlining.
"""

from __future__ import print_function, absolute_import

import numpy as np

import numba
from numba import njit, ir, objmode
from numba.extending import overload
from numba.ir_utils import dead_code_elimination, resolve_func_from_module
from itertools import product, combinations
from .support import TestCase, unittest


class InlineTestPipeline(numba.compiler.BasePipeline):
    """ Same as the standard pipeline, but preserves the func_ir into the
    metadata store"""

    def stage_preserve_final_ir(self):
        self.metadata['final_func_ir'] = self.func_ir.copy()

    def stage_dce(self):
        dead_code_elimination(self.func_ir, self.typemap)

    def define_pipelines(self, pm):
        self.define_nopython_pipeline(pm)
        # mangle the default pipeline and inject DCE and IR preservation ahead
        # of legalisation
        allstages = pm.pipeline_stages['nopython']
        new_pipe = []
        for x in allstages:
            if x[0] == self.stage_ir_legalization:
                new_pipe.append((self.stage_dce, "DCE"))
                new_pipe.append((self.stage_preserve_final_ir, "preserve IR"))
            new_pipe.append(x)
        pm.pipeline_stages['nopython'] = new_pipe


# this global has the same name as the the global in inlining_usecases.py, it
# is here to check that inlined functions bind to their own globals
_GLOBAL1 = -50


class InliningBase(TestCase):

    _DEBUG = False

    inline_opt_as_bool = {'always': True, 'never': False}

    #---------------------------------------------------------------------------
    # Example cost model

    def sentinel_17_cost_model(self, func_ir):
        # sentinel 17 cost model, this is a fake cost model that will return
        # True (i.e. inline) if the ir.FreeVar(17) is found in the func_ir,
        for blk in func_ir.blocks.values():
            for stmt in blk.body:
                if isinstance(stmt, ir.Assign):
                    if isinstance(stmt.value, ir.FreeVar):
                        if stmt.value.value == 17:
                            return True
        return False

    def s17_caller_model(self, caller_info, callee_info):
        return self.sentinel_17_cost_model(caller_info)

    def s17_callee_model(self, caller_info, callee_info):
        return self.sentinel_17_cost_model(callee_info)

    #---------------------------------------------------------------------------

    def check(self, test_impl, *args, inline_expect=None, block_count=1):
        assert inline_expect
        for k, v in inline_expect.items():
            assert isinstance(k, str)
            assert isinstance(v, bool)

        j_func = njit(pipeline_class=InlineTestPipeline)(test_impl)

        # check they produce the same answer first!
        self.assertEqual(test_impl(*args), j_func(*args))

        # make sure IR doesn't have branches
        fir = j_func.overloads[j_func.signatures[0]].metadata['final_func_ir']
        fir.blocks = numba.ir_utils.simplify_CFG(fir.blocks)
        if self._DEBUG:
            print("FIR".center(80, "-"))
            fir.dump()
        self.assertEqual(len(fir.blocks), block_count)
        block = next(iter(fir.blocks.values()))

        # if we don't expect the function to be inlined then make sure there is
        # 'call' present still
        exprs = [x for x in block.find_exprs()]
        assert exprs
        for k, v in inline_expect.items():
            found = False
            for expr in exprs:
                if getattr(expr, 'op', False) == 'call':
                    func_defn = fir.get_definition(expr.func)
                    found |= func_defn.name == k
            try:
                self.assertFalse(found == v)
            except:
                breakpoint()
                pass


# used in _gen_involved
_GLOBAL = 1234


def _gen_involved():
    _FREEVAR = 0xCAFE

    def foo(a, b, c=12, d=1j, e=None):
        f = a + b
        a += _FREEVAR
        g = np.zeros(c, dtype=np.complex64)
        h = f + g
        i = 1j / d
        if np.abs(i) > 0:
            k = h / i
            l = np.arange(1, c + 1)
            m = np.sqrt(l - g) + e  * k
            if np.abs(m[0]) < 1:
                n = 0
                for o in range(a):
                    n += 0
                    if np.abs(n) < 3:
                        break
                n += m[2]
            p = g / l
            q = []
            for r in range(len(p)):
                q.append(p[r])
                if r > 4 + 1:
                    s = 123
                    t = 5
                    if s > 122 - c:
                        t += s
                t += q[0] + _GLOBAL

        return f + o + r + t + r + a + n

    return foo


class TestFunctionInlining(InliningBase):

    def test_basic_inline_never(self):
        @njit(inline='never')
        def foo():
            return

        def impl():
            return foo()
        self.check(impl, inline_expect={'foo': False})

    def test_basic_inline_always(self):
        @njit(inline='always')
        def foo():
            return

        def impl():
            return foo()
        self.check(impl, inline_expect={'foo': True})

    def test_basic_inline_combos(self):

        def impl():
            x = foo()
            y = bar()
            z = baz()
            return x, y, z

        opts = (('always'), ('never'))

        for inline_foo, inline_bar, inline_baz in product(opts, opts, opts):

            @njit(inline=inline_foo)
            def foo():
                return

            @njit(inline=inline_bar)
            def bar():
                return

            @njit(inline=inline_baz)
            def baz():
                return

            inline_expect = {'foo': self.inline_opt_as_bool[inline_foo],
                             'bar': self.inline_opt_as_bool[inline_bar],
                             'baz': self.inline_opt_as_bool[inline_baz]}
            self.check(impl, inline_expect=inline_expect)

    @unittest.skip("Need to work out how to prevent this")
    def test_recursive_inline(self):

        @njit(inline='always')
        def foo(x):
            if x == 0:
                return 12
            else:
                foo(x - 1)

        a = 3

        def impl():
            b = 0
            if a > 1:
                b += 1
            foo(5)
            if b < a:
                b -= 1

        self.check(impl, inline_expect={'foo': True})

    def test_freevar_bindings(self):

        def factory(inline, x, y):
            z = x + 12
            @njit(inline=inline)
            def func():
                return (x, y + 3, z)
            return func

        def impl():
            x = foo()
            y = bar()
            z = baz()
            return x, y, z

        opts = (('always'), ('never'))

        for inline_foo, inline_bar, inline_baz in product(opts, opts, opts):

            foo = factory(inline_foo, 10, 20)
            bar = factory(inline_bar, 30, 40)
            baz = factory(inline_baz, 50, 60)

            inline_expect = {'foo': self.inline_opt_as_bool[inline_foo],
                             'bar': self.inline_opt_as_bool[inline_bar],
                             'baz': self.inline_opt_as_bool[inline_baz]}
            self.check(impl, inline_expect=inline_expect)

    def test_inline_from_another_module(self):

        from .inlining_usecases import bar

        def impl():
            z = _GLOBAL1 + 2
            return bar(), z

        self.check(impl, inline_expect={'bar': True})

    def test_inline_from_another_module_w_getattr(self):

        import numba.tests.inlining_usecases as iuc

        def impl():
            z = _GLOBAL1 + 2
            return iuc.bar(), z

        self.check(impl, inline_expect={'bar': True})

    def test_inline_from_another_module_w_2_getattr(self):

        import numba.tests.inlining_usecases  # forces registration
        import numba.tests as nt

        def impl():
            z = _GLOBAL1 + 2
            return nt.inlining_usecases.bar(), z

        self.check(impl, inline_expect={'bar': True})

    def test_inline_from_another_module_as_freevar(self):

        def factory():
            from .inlining_usecases import bar
            @njit(inline='always')
            def tmp():
                return bar()
            return tmp

        baz = factory()

        def impl():
            z = _GLOBAL1 + 2
            return baz(), z

        self.check(impl, inline_expect={'bar': True})

    def test_inline_w_freevar_from_another_module(self):

        from .inlining_usecases import baz_factory

        def gen(a, b):
            bar = baz_factory(a)

            def impl():
                z = _GLOBAL1 + a * b
                return bar(), z, a
            return impl

        impl = gen(10, 20)
        self.check(impl, inline_expect={'bar': True})

    def test_inlining_models(self):

        # caller has sentinel
        for caller, callee in ((10, 11), (17, 11)):

            @njit(inline=self.s17_caller_model)
            def foo():
                return callee

            def impl(z):
                x = z + caller
                y = foo()
                return y + 3, x

            self.check(impl, 10, inline_expect={'foo': caller == 17})

        # callee has sentinel
        for caller, callee in ((11, 17), (11, 10)):

            @njit(inline=self.s17_callee_model)
            def foo():
                return callee

            def impl(z):
                x = z + caller
                y = foo()
                return y + 3, x

            self.check(impl, 10, inline_expect={'foo': callee == 17})

    def test_inline_inside_loop(self):
        @njit(inline='always')
        def foo():
            return 12

        def impl():
            acc = 0.0
            for i in range(5):
                acc += foo()
            return acc

        self.check(impl, inline_expect={'foo': True}, block_count=4)

    def test_inline_inside_closure_inside_loop(self):
        @njit(inline='always')
        def foo():
            return 12

        def impl():
            acc = 0.0
            for i in range(5):
                def bar():
                    return foo() + 7
                acc += bar()
            return acc

        self.check(impl, inline_expect={'foo': True}, block_count=4)

    def test_inline_closure_inside_inlinable_inside_closure(self):
        @njit(inline='always')
        def foo(a):
            def baz():
                return 12 + a
            return baz() + 8

        def impl():
            z = 9
            def bar(x):
                return foo(z) + 7 + x
            return bar(z + 2)

        self.check(impl, inline_expect={'foo': True}, block_count=1)


    def test_inline_involved(self):

        fortran = njit(inline='always')(_gen_involved())

        @njit(inline='always')
        def boz(j):
            acc = 0
            def biz(t):
                return t + acc
            for x in range(j):
                acc += biz(8 + acc) + fortran(2., acc, 1, 12j, biz(acc))
            return acc

        @njit(inline='always')
        def foo(a):
            acc = 0
            for p in range(12):
                tmp = fortran(1, 1, 1, 1, 1)
                def baz(x):
                    return 12 + a + x + tmp
                acc += baz(p) + 8 + boz(p) + tmp
            return acc + baz(2)

        def impl():
            z = 9
            def bar(x):
                return foo(z) + 7 + x
            return bar(z + 2)

        self.check(impl, inline_expect={'foo': True, 'boz': True,
                                        'fortran': True}, block_count=37)


class TestOverloadInlining(InliningBase):

    def test_basic_inline_never(self):
        def foo():
            pass

        @overload(foo, inline='never')
        def foo_overload():
            def foo_impl():
                pass
            return foo_impl

        def impl():
            return foo()

        self.check(impl, inline_expect={'foo': False})

    def test_basic_inline_always(self):

        def foo():
            pass

        @overload(foo, inline='always')
        def foo_overload():
            def impl():
                pass
            return impl

        def impl():
            return foo()

        self.check(impl, inline_expect={'foo': True})

    def test_basic_inline_combos(self):

        def impl():
            x = foo()
            y = bar()
            z = baz()
            return x, y, z

        opts = (('always'), ('never'))

        for inline_foo, inline_bar, inline_baz in product(opts, opts, opts):

            def foo():
                pass

            def bar():
                pass

            def baz():
                pass

            @overload(foo, inline=inline_foo)
            def foo_overload():
                def impl():
                    return
                return impl

            @overload(bar, inline=inline_bar)
            def bar_overload():
                def impl():
                    return
                return impl

            @overload(baz, inline=inline_baz)
            def baz_overload():
                def impl():
                    return
                return impl

            inline_expect = {'foo': self.inline_opt_as_bool[inline_foo],
                             'bar': self.inline_opt_as_bool[inline_bar],
                             'baz': self.inline_opt_as_bool[inline_baz]}
            self.check(impl, inline_expect=inline_expect)

    def test_freevar_bindings(self):

        def impl():
            x = foo()
            y = bar()
            z = baz()
            return x, y, z

        opts = (('always'), ('never'))

        for inline_foo, inline_bar, inline_baz in product(opts, opts, opts):
            # need to repeatedly clobber definitions of foo, bar, baz so
            # @overload binds to the right instance WRT inlining

            def foo():
                x = 10
                y = 20
                z = x + 12
                return (x, y + 3, z)

            def bar():
                x = 30
                y = 40
                z = x + 12
                return (x, y + 3, z)

            def baz():
                x = 60
                y = 80
                z = x + 12
                return (x, y + 3, z)

            def factory(target, x, y, inline=None):
                z = x + 12
                @overload(target, inline=inline)
                def func():
                    def impl():
                        return (x, y + 3, z)
                    return impl

            factory(foo, 10, 20, inline=inline_foo)
            factory(bar, 30, 40, inline=inline_bar)
            factory(baz, 60, 80, inline=inline_baz)

            inline_expect = {'foo': self.inline_opt_as_bool[inline_foo],
                             'bar': self.inline_opt_as_bool[inline_bar],
                             'baz': self.inline_opt_as_bool[inline_baz]}

            self.check(impl, inline_expect=inline_expect)

    def test_inline_from_another_module(self):

        from .inlining_usecases import baz

        def impl():
            z = _GLOBAL1 + 2
            return baz(), z

        self.check(impl, inline_expect={'baz': True})

    def test_inline_from_another_module_w_getattr(self):

        import numba.tests.inlining_usecases as iuc

        def impl():
            z = _GLOBAL1 + 2
            return iuc.baz(), z

        self.check(impl, inline_expect={'baz': True})

    def test_inline_from_another_module_w_2_getattr(self):

        import numba.tests.inlining_usecases  # forces registration
        import numba.tests as nt

        def impl():
            z = _GLOBAL1 + 2
            return nt.inlining_usecases.baz(), z

        self.check(impl, inline_expect={'baz': True})

    def test_inline_from_another_module_as_freevar(self):

        def factory():
            from .inlining_usecases import baz
            @njit(inline='always')
            def tmp():
                return baz()
            return tmp

        bop = factory()

        def impl():
            z = _GLOBAL1 + 2
            return bop(), z

        self.check(impl, inline_expect={'baz': True})

    def test_inline_w_freevar_from_another_module(self):

        from .inlining_usecases import bop_factory

        def gen(a, b):
            bar = bop_factory(a)

            def impl():
                z = _GLOBAL1 + a * b
                return bar(), z, a
            return impl

        impl = gen(10, 20)
        self.check(impl, inline_expect={'bar': True})

    def test_inlining_models(self):

        # caller has sentinel
        for caller, callee in ((10, 11), (17, 11)):

            def foo():
                return callee

            @overload(foo, inline=self.s17_caller_model)
            def foo_ol():
                def impl():
                    return callee
                return impl

            def impl(z):
                x = z + caller
                y = foo()
                return y + 3, x

            self.check(impl, 10, inline_expect={'foo': caller == 17})

        # callee has sentinel
        for caller, callee in ((11, 17), (11, 10)):

            def foo():
                return callee

            @overload(foo, inline=self.s17_callee_model)
            def foo_ol():
                def impl():
                    return callee
                return impl

            def impl(z):
                x = z + caller
                y = foo()
                return y + 3, x

            self.check(impl, 10, inline_expect={'foo': callee == 17})
