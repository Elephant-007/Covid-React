# -*- coding: utf-8 -*-

"""
Generate Python classes and Cython pxd files.
"""

from __future__ import print_function, division, absolute_import

from . import generator
from numba.asdl.schema import verify_schema_keywords


class Class(object):
    def __init__(self, name, base, doc, fields=(), attributes=None):
        self.name = name
        self.base = base
        self.doc = doc
        self.fields = fields
        self.attributes = attributes


class ClassCodegen(generator.SimpleCodegen):
    """
    Generate Python AST nodes.
    """

    def __init__(self, out_filename, preamble, Class, rootclass):
        super(ClassCodegen, self).__init__(out_filename)
        self.preamble = preamble
        self.rootclass = rootclass
        self.Class = Class

    def emit_preamble(self, emitter, schema):
        verify_schema_keywords(schema)
        emitter.emit(self.preamble)
        emitter.emit(self.rootclass)

    def emit_rule(self, emitter, schema, rulename, rule):
        "Emit code for a rule (a nonterminal)"
        nonterminal_fields = schema.attributes[rulename]
        attrs = tuple(map(repr, (field.name for field in nonterminal_fields)))

        c = self.Class(
            rulename,
            self.rootclass.name,
            doc=str(rule),
            # Attributes: Use None instead of empty tuple to inherit from base
            attributes=attrs or None,
        )
        emitter.emit(c)

    def emit_sum(self, emitter, schema, rulename, rule, sumtype):
        fields = schema.types[sumtype]
        emitter.emit(self.Class(sumtype, rulename, doc=sumtype, fields=fields))
