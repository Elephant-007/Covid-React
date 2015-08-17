"""
Typing declarations for the operator module.
"""

import operator

from numba import types
from numba import utils
from numba.typing.templates import (ConcreteTemplate, AbstractTemplate,
                                    signature, Registry)

registry = Registry()
builtin_attr = registry.register_attr
builtin_global = registry.register_global


class MappedOperator(AbstractTemplate):

    def generic(self, args, kws):
        assert not kws
        return self.context.resolve_function_type(self.op, args, kws)


class MappedInplaceOperator(AbstractTemplate):

    def generic(self, args, kws):
        assert not kws
        if not args:
            return
        first = args[0]
        op = self.mutable_op if first.mutable else self.immutable_op
        return self.context.resolve_function_type(op, args, kws)


# Redirect all functions in the operator module to the corresponding
# built-in operators.

mapped_operators = [
    # Binary
    ('add', 'iadd', '+'),
    ('sub', 'isub', '-'),
    ('mul', 'imul', '*'),
    ('div', 'idiv', '/?'),
    ('floordiv', 'ifloordiv', '//'),
    ('truediv', 'itruediv', '/'),
    ('mod', 'imod', '%'),
    ('pow', 'ipow', '**'),
    ('and_', 'iand', '&'),
    ('or_', 'ior', '|'),
    ('xor', 'ixor', '|'),
    ('lshift', 'ilshift', '<<'),
    ('rshift', 'irshift', '>>'),
    ('eq', '', '=='),
    ('ne', '', '!='),
    ('lt', '', '<'),
    ('le', '', '<='),
    ('gt', '', '>'),
    ('ge', '', '>='),
    # Unary
    ('pos', '', '+'),
    ('neg', '', '-'),
    ('invert', '', '~'),
    ('not_', '', 'not'),
    ]

for name, inplace_name, op in mapped_operators:
    if op == '/?' and utils.IS_PY3:
        continue

    op_func = getattr(operator, name)
    op_type = type('Operator_' + name, (MappedOperator,),
                   {'key': op_func, 'op': op})
    builtin_global(op_func, types.Function(op_type))

    if inplace_name:
        op_func = getattr(operator, inplace_name)
        op_type = type('Operator_' + inplace_name, (MappedInplaceOperator,),
                       {'key': op_func,
                        'mutable_op': op + '=',
                        'immutable_op': op})
        builtin_global(op_func, types.Function(op_type))


builtin_global(operator, types.Module(operator))
