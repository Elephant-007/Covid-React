# -*- coding: utf-8 -*-

"""
Generate Python visitors and Cython pxd files.
"""

from __future__ import print_function, division, absolute_import

import os

from . import generator
from .formatting import format_stats, get_fields

#------------------------------------------------------------------------
# Code Formatting
#------------------------------------------------------------------------

interface_class = '''

def iter_fields(node):
    """
    Yield a tuple of ``(fieldname, value)`` for each field in ``node._fields``
    that is present on *node*.
    """
    result = []
    for field in node._fields:
        try:
            result.append((field, getattr(node, field)))
        except AttributeError:
            pass

    return result

class GenericVisitor(object):

    def visit(self, node):
        return node.accept(self)

    def generic_visit(self, node):
        """Called explicitly by the user from an overridden visitor method"""
        raise NotImplementedError

'''

pxd_interface_class = """\
from nodes cimport *

cdef class GenericVisitor(object):
    cpdef generic_visit(self, node)
"""


# TODO: We can also make 'visitchildren' dispatch quickly

visitor_class = '''
from interface import GenericVisitor, iter_fields
from nodes import AST

class Visitor(GenericVisitor):

    def generic_visit(self, node):
        for field, value in iter_fields(node):
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, AST):
                        item.accept(self)
            elif isinstance(value, AST):
                value.accept(self)

'''

transformer_class = """
from interface import GenericVisitor, iter_fields
from nodes import AST

class Transformer(GenericVisitor):

    def generic_visit(self, node):
        for field, old_value in iter_fields(node):
            old_value = getattr(node, field, None)
            if isinstance(old_value, list):
                new_values = []
                for value in old_value:
                    if isinstance(value, AST):
                        value = value.accept(self)
                        if value is None:
                            continue
                        elif not isinstance(value, AST):
                            new_values.extend(value)
                            continue
                    new_values.append(value)
                old_value[:] = new_values
            elif isinstance(old_value, AST):
                new_node = old_value.accept(self)
                if new_node is None:
                    delattr(node, field)
                else:
                    setattr(node, field, new_node)
        return node

"""

pxd_visitor_class = """
from interface import GenericVisitor

cdef class Visitor(GenericVisitor):
    pass
"""

pxd_transformer_class = """
from interface import GenericVisitor

cdef class Transformer(GenericVisitor):
    pass
"""

#------------------------------------------------------------------------
# Code Formatting
#------------------------------------------------------------------------

def make_visit_stats(schema, fields, inplace):
    stats = []
    for field, field_access in zip(fields, get_fields(fields, obj="node")):
        field_type = str(field.type)
        if field_type not in schema.dfns and field_type not in schema.types:
            # Not an AST node
            continue

        s = "%s.accept(self)" % field_access

        if inplace:
            # Mutate in-place (transform)
            s = "%s = %s" % (field_access, s)

        if field.opt:
            # Guard for None
            s = "if %s is not None: %s" % (field_access, s)

        stats.append(s)

    if inplace:
        stats.append("return node")

    return stats or ["pass"]

#------------------------------------------------------------------------
# Method Generation
#------------------------------------------------------------------------

class Method(object):
    def __init__(self, schema, name, fields):
        self.schema = schema
        self.name = name
        self.fields = fields

class InterfaceMethod(Method):
    def __str__(self):
        return (
           "    def visit_%s(self, node):\n"
           "        raise NotImplementedError\n"
           "\n"
       ) % (self.name,)

class PyMethod(Method):

    inplace = None

    def __str__(self):
        stats = make_visit_stats(self.schema, self.fields, self.inplace)
        return (
           "    def visit_%s(self, node):\n"
           "        %s\n"
           "\n"
       ) % (self.name, format_stats("\n", 8, stats))

class PyVisitMethod(PyMethod):
    inplace = False

class PyTransformMethod(PyVisitMethod):
    inplace = True

class PxdMethod(Method):
    def __str__(self):
        return "    cpdef visit_%s(self, %s node)\n" % (self.name, self.name)

#------------------------------------------------------------------------
# Code Generators
#------------------------------------------------------------------------

class VisitorCodeGen(generator.Codegen):
    """
    Generate Python AST nodes.
    """

    def __init__(self, out_filename, preamble, Method):
        super(VisitorCodeGen, self).__init__(out_filename)
        self.preamble = preamble
        self.Method = Method

    def generate(self, emitter, asdl_tree, schema):
        emitter.emit(self.preamble)
        for rulename, rule in schema.dfns.iteritems():
            self.emit_rule(emitter, schema, rulename, rule)

    def emit_rule(self, emitter, schema, rulename, rule):
        "Emit code for a rule (a nonterminal)"
        if rule.is_sum:
            for sumtype in rule.fields:
                self.emit_sum(emitter, schema, sumtype)

    def emit_sum(self, emitter, schema, sumtype):
        fields = schema.types[sumtype]
        emitter.emit(self.Method(schema, sumtype, fields))

#------------------------------------------------------------------------
# Global Exports
#------------------------------------------------------------------------

codegens = [
    VisitorCodeGen("interface.py", interface_class, InterfaceMethod),
    VisitorCodeGen("interface.pxd", pxd_interface_class, PxdMethod),
    VisitorCodeGen("visitor.py", visitor_class, PyVisitMethod),
    generator.UtilityCodeGen("visitor.pxd", pxd_visitor_class),
    VisitorCodeGen("transformer.py", transformer_class, PyTransformMethod),
    generator.UtilityCodeGen("transformer.pxd", pxd_transformer_class),
]

if __name__ == '__main__':
    root = os.path.dirname(os.path.abspath(__file__))
    testdir = os.path.join(root, "tests")
    schema_filename = os.path.join(testdir, "testschema1.asdl")
    generator.generate_from_file(schema_filename, codegens, root)