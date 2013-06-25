# -*- coding: utf-8 -*-

"""
Control flow for the AST backend.

Adapted from Cython/Compiler/FlowControl.py
"""
from __future__ import print_function, division, absolute_import

import re
import ast
import copy
from functools import reduce

from numba import error, visitors, symtab, nodes, reporting

from numba import *
from numba.control_flow import  graphviz, reaching
from numba.control_flow.cfstats import *
from numba.control_flow.debug import *


class ControlBlock(nodes.LowLevelBasicBlockNode):
    """
    Control flow graph node. Sequence of assignments and name references.
    This is simultaneously an AST node.

       children  set of children nodes
       parents   set of parent nodes
       positions set of position markers

       stats     list of block statements
       gen       dict of assignments generated by this block
       bound     set  of entries that are definitely bounded in this block

       Example:

        a = 1
        b = a + c # 'c' is already bounded or exception here

        stats = [Assignment(a), NameReference(a), NameReference(c),
                     Assignment(b)]
        gen = {Entry(a): Assignment(a), Entry(b): Assignment(b)}
        bound = set([Entry(a), Entry(c)])
    """

    _fields = ['phi_nodes', 'body']

    def __init__(self, id, label='empty', have_code=True,
                 is_expr=False, is_exit=False, pos=None,
                 is_fabricated=False):
        if pos:
            label = "%s_%s" % (label, error.format_pos(pos).rstrip(": "))
        super(ControlBlock, self).__init__(body=[], label=label)

        self.id = id

        self.children = set()
        self.parents = set()
        self.positions = set()

        self.stats = []
        self.gen = {}
        self.bound = set()

        # Same as i_input/i_output but for reaching defs with sets
        self.input = set()
        self.output = set()

        self.i_input = 0
        self.i_output = 0
        self.i_gen = 0
        self.i_kill = 0
        self.i_state = 0

        self.is_expr = is_expr
        self.is_exit = is_exit
        self.have_code = have_code

        # TODO: Make these bits
        # Set of blocks that dominate this block
        self.dominators = set()
        # Set of blocks where our dominance stops
        self.dominance_frontier = set()
        # SSA Φ locations. Maps Variables to a list of (basic_block, definition)
        # There can be only one reaching definition, since each variable is
        # assigned only once
        self.phis = {}
        self.phi_nodes = []

        # Promotions at the end of the block to have a consistent promoted
        # Φ type at one of our children.
        self.promotions = {} # (renamed_var_name, dst_type) -> promotion_node

        # LLVM entry and exit blocks. The entry block is the block before the
        # body is evaluated, the exit block the block after the body is
        # evaluated.
        self.exit_block = None
        self.phi_block = None
        self.exit_block = None
        self.promotions = set()

        self.symtab = None
        self.is_fabricated = is_fabricated
        # If set to True, branch from the previous basic block to this basic
        # block
        self.branch_here = False

    def empty(self):
        return (not self.stats and not self.positions and not self.phis)

    def detach(self):
        """Detach block from parents and children."""
        for child in self.children:
            child.parents.remove(self)
        for parent in self.parents:
            parent.children.remove(self)
        self.parents.clear()
        self.children.clear()

    def add_child(self, block):
        self.children.add(block)
        block.parents.add(self)

    def reparent(self, new_block):
        """
        Re-parent all children to the new block
        """
        for child in self.children:
            child.parents.remove(self)
            new_block.add_child(child)

    def delete(self, flow):
        """
        Delete a block from the cfg.
        """
        for parent in self.parents:
            parent.children.remove(self)
        for child in self.children:
            child.parents.remove(self)

        flow.blocks.remove(self)

    def __repr__(self):
        return 'Block(%d)' % self.id

    def __getattr__(self, attr):
        if attr in ('variable', 'type', 'ctx'):
            return getattr(self.body[0], attr)
        raise AttributeError

    def __setattr__(self, attr, value):
        if attr in ('variable', 'type'):
            setattr(self.body[0], attr, value)
        else:
            super(ControlBlock, self).__setattr__(attr, value)


class ExitBlock(ControlBlock):
    """Non-empty exit point block."""

    def empty(self):
        return False


class AssignmentList:
    def __init__(self):
        self.stats = []


class ControlFlow(object):
    """
    Control-flow graph.

       entry_point ControlBlock entry point for this graph
       exit_point  ControlBlock normal exit point
       block       ControlBlock current block
       blocks      set    children nodes
       entries     set    tracked entries
       loops       list   stack for loop descriptors
       exceptions  list   stack for exception descriptors

    """

    def __init__(self, env, source_descr):
        self.env = env
        self.source_descr = source_descr

        self.blocks = []
        self.entries = set()
        self.loops = []
        self.exceptions = []

        self.entry_point = ControlBlock(-1, label='entry')
        self.exit_point = ExitBlock(0, label='exit')
        self.block = self.entry_point

    def newblock(self, parent=None, **kwargs):
        """
        Create floating block linked to `parent` if given.
        Does NOT set the current block to the new block.
        """
        block = ControlBlock(len(self.blocks), **kwargs)
        self.blocks.append(block)
        if parent:
            parent.add_child(block)

        return block

    def nextblock(self, parent=None, **kwargs):
        """
        Create child block linked to current or `parent` if given.
        Sets the current block to the new block.
        """
        block = self.newblock(parent, **kwargs)
        if not parent and self.block:
            self.block.add_child(block)

        self.block = block
        return block

    def exit_block(self, parent=None, **kwargs):
        """
        Create a floating exit block. This can later be added to self.blocks.
        This is useful to ensure topological order.
        """
        block = self.newblock(parent, have_code=False, is_exit=True, **kwargs)
        self.blocks.pop()
        return block

    def add_exit(self, exit_block):
        "Add an exit block after visiting the body"
        exit_block.id = len(self.blocks)
        self.blocks.append(exit_block)

    def is_listcomp_var(self, name):
        return re.match(r"_\[\d+\]", name)

    def is_tracked(self, entry):
        return (# entry.renameable and not
                entry.name not in self.env.translation.crnt.locals and not
                self.is_listcomp_var(entry.name))

    def mark_position(self, node):
        """Mark position, will be used to draw graph nodes."""
        if self.block:
            src_descr = self.source_descr
            pos = (src_descr,) + getpos(node)
            self.block.positions.add(pos)

    def mark_assignment(self, lhs, rhs, entry, assignment, warn_unused=True):
        if self.block:
            if not self.is_tracked(entry):
                return
            assignment = NameAssignment(lhs, rhs, entry, assignment,
                                        warn_unused=warn_unused)
            self.block.stats.append(assignment)
            self.block.gen[entry] = assignment
            self.entries.add(entry)
            return assignment

    def mark_argument(self, lhs, rhs, entry):
        if self.block and self.is_tracked(entry):
            assignment = Argument(lhs, rhs, entry)
            self.block.stats.append(assignment)
            self.block.gen[entry] = assignment
            self.entries.add(entry)

    def mark_deletion(self, node, entry):
        if self.block and self.is_tracked(entry):
            assignment = NameDeletion(node, entry)
            self.block.stats.append(assignment)
            self.block.gen[entry] = Uninitialized
            self.entries.add(entry)

    def mark_reference(self, node, entry):
        if self.block and self.is_tracked(entry):
            self.block.stats.append(NameReference(node, entry))
            # Local variable is definitely bound after this reference
            if not reaching.allow_null(node):
                self.block.bound.add(entry)
            self.entries.add(entry)

    def normalize(self):
        """Delete unreachable and orphan blocks."""
        blocks = set(self.blocks)
        queue = set([self.entry_point])
        visited = set()
        while queue:
            root = queue.pop()
            visited.add(root)
            for child in root.children:
                if child not in visited:
                    queue.add(child)
        unreachable = blocks - visited
        for block in unreachable:
            block.detach()
        visited.remove(self.entry_point)
        for block in visited:
            if block.empty():
                for parent in block.parents: # Re-parent
                    for child in block.children:
                        parent.add_child(child)
                block.detach()
                unreachable.add(block)
        blocks -= unreachable
        self.blocks = [block for block in self.blocks if block in blocks]

    def initialize(self):
        """Set initial state, map assignments to bits."""
        self.assmts = {}

        offset = 0
        for entry in self.entries:
            assmts = AssignmentList()
            assmts.bit = 1 << offset
            assmts.mask = assmts.bit
            self.assmts[entry] = assmts
            offset += 1

        for block in self.blocks:
            block.stats = block.phis.values() + block.stats
            for stat in block.stats:
                if isinstance(stat, (PhiNode, NameAssignment)):
                    stat.bit = 1 << offset
                    assmts = self.assmts[stat.entry]
                    assmts.stats.append(stat)
                    assmts.mask |= stat.bit
                    offset += 1

        for block in self.blocks:
            for entry, stat in block.gen.items():
                assmts = self.assmts[entry]
                if stat is Uninitialized:
                    block.i_gen |= assmts.bit
                else:
                    block.i_gen |= stat.bit
                block.i_kill |= assmts.mask
            block.i_output = block.i_gen
            for entry in block.bound:
                block.i_kill |= self.assmts[entry].bit

        for assmts in self.assmts.itervalues():
            self.entry_point.i_gen |= assmts.bit
        self.entry_point.i_output = self.entry_point.i_gen

    def map_one(self, istate, entry):
        "Map the bitstate of a variable to the definitions it represents"
        ret = set()
        assmts = self.assmts[entry]
        if istate & assmts.bit:
            ret.add(Uninitialized)
        for assmt in assmts.stats:
            if istate & assmt.bit:
                ret.add(assmt)
        return ret

    def reaching_definitions(self):
        """Per-block reaching definitions analysis."""
        dirty = True
        while dirty:
            dirty = False
            for block in self.blocks:
                i_input = 0
                for parent in block.parents:
                    i_input |= parent.i_output
                i_output = (i_input & ~block.i_kill) | block.i_gen
                if i_output != block.i_output:
                    dirty = True
                block.i_input = i_input
                block.i_output = i_output

    def initialize_sets(self):
        """
        Set initial state, run after SSA. There is only ever one live
        definition of a variable in a block, so we can simply track input
        and output definitions as the Variable/Entry they came as.
        """
        for block in self.blocks:
            # Insert phi nodes from SSA stage into the assignments of the block
            for phi in block.phis:
                block.gen.setdefault(phi, []).insert(0, phi)

            # Update the kill set with the variables that are assigned to in
            # the block
            block.kill = set(block.gen)
            block.output = set(block.gen)
            #for entry in block.bound:
            #    block.i_kill |= self.assmts[entry].bit

        for assmts in self.assmts.itervalues():
            self.entry_point.i_gen |= assmts.bit
        self.entry_point.i_output = self.entry_point.i_gen

    def compute_dominators(self):
        """
        Compute the dominators for the CFG, i.e. for each basic block the
        set of basic blocks that dominate that block. This mean from the
        entry block to that block must go through the blocks in the dominator
        set.

        dominators(x) = {x} ∪ (∩ dominators(y) for y ∈ preds(x))
        """
        blocks = set(self.blocks)
        for block in self.blocks:
            block.dominators = blocks

        changed = True
        while changed:
            changed = False
            for block in self.blocks:
                parent_dominators = [parent.dominators for parent in block.parents]
                new_doms = set.intersection(block.dominators, *parent_dominators)
                new_doms.add(block)

                if new_doms != block.dominators:
                    block.dominators = new_doms
                    changed = True

    def immediate_dominator(self, x):
        """
        The dominator of x that is dominated by all other dominators of x.
        This is the block that has the largest dominator set.
        """
        candidates = x.dominators - set([x])
        if not candidates:
            return None

        result = max(candidates, key=lambda b: len(b.dominators))
        ndoms = len(result.dominators)
        assert len([b for b in candidates if len(b.dominators) == ndoms]) == 1
        return result

    def compute_dominance_frontier(self):
        """
        Compute the dominance frontier for all blocks. This indicates for
        each block where dominance stops in the CFG. We use this as the place
        to insert Φ functions, since at the dominance frontier there are
        multiple control flow paths to the block, which means multiple
        variable definitions can reach there.
        """
        if debug:
            print("Dominator sets:")
            for block in self.blocks:
                print((block.id, sorted(block.dominators, key=lambda b: b.id)))

        blocks = []
        for block in self.blocks:
            if block.parents:
                block.idom = self.immediate_dominator(block)
                block.visited = False
                blocks.append(block)

        self.blocks = blocks

        def visit(block, result):
            block.visited = True
            for child in block.children:
                if not child.visited:
                    visit(child, result)
            result.append(block)

        #postorder = []
        #visit(self.blocks[0], postorder)
        postorder = self.blocks[::-1]

        # Compute dominance frontier
        for x in postorder:
            for y in x.children:
                if y.idom is not x:
                    # We are not an immediate dominator of our successor, add
                    # to frontier
                    x.dominance_frontier.add(y)

            for z in self.blocks:
                if z.idom is x:
                    for y in z.dominance_frontier:
                        if y.idom is not x:
                            x.dominance_frontier.add(y)

    def update_for_ssa(self, ast, symbol_table):
        """
        1) Compute phi nodes

            for each variable v
                1) insert empty phi nodes in dominance frontier of each block
                   that defines v
                2) this phi defines a new assignment in each block in which
                   it is inserted, so propagate (recursively)

        2) Reaching definitions

            Set block-local symbol table for each block.
            This is a rudimentary form of reaching definitions, but we can
            do it in a single pass because all assignments are known (since
            we inserted the phi functions, which also count as assignments).
            This means the output set is known up front for each block
            and never changes. After setting all output sets, we can compute
            the input sets in a single pass:

                1) compute output sets for each block
                2) compute input sets for each block

        3) Update phis with incoming variables. The incoming variables are
           last assignments of the predecessor blocks in the CFG.
        """
        # Print dominance frontier
        if debug:
            print("Dominance frontier:")
            for block in self.blocks:
                print(('DF(%d) = %s' % (block.id, block.dominance_frontier)))

        argnames = [name.id for name in ast.args.args]

        #
        ### 1) Insert phi nodes in the right places
        #
        for name, variable in symbol_table.iteritems():
            if not variable.renameable:
                continue

            defining = []
            for b in self.blocks:
                if variable in b.gen:
                    defining.append(b)

            for defining_block in defining:
                for f in defining_block.dominance_frontier:
                    phi = f.phis.get(variable, None)
                    if phi is None:
                        phi = PhiNode(f, variable)
                        f.phis[variable] = phi
                        defining.append(f)

        #
        ### 2) Reaching definitions and variable renaming
        #

        # Set originating block for each variable (as if each variable were
        # initialized at the start of the function) and start renaming of
        # variables
        symbol_table.counters = dict.fromkeys(symbol_table, -1) # var_name -> counter
        self.blocks[0].symtab = symbol_table
        for var_name, var in symbol_table.items():
            if var.renameable:
                new_var = symbol_table.rename(var, self.blocks[0])
                new_var.uninitialized = var.name not in argnames

        self.rename_assignments(self.blocks[0])

        for block in self.blocks[1:]:
            block.symtab = symtab.Symtab(parent=block.idom.symtab)
            for var, phi_node in block.phis.iteritems():
                phi_node.variable = block.symtab.rename(var, block)
                phi_node.variable.name_assignment = phi_node
                phi_node.variable.is_phi = True

            self.rename_assignments(block)

        #
        ### 3) Update the phis with all incoming entries
        #
        for block in self.blocks:
            # Insert phis in AST
            block.phi_nodes = block.phis.values()
            for variable, phi in block.phis.iteritems():
                for parent in block.parents:
                    incoming_var = parent.symtab.lookup_most_recent(variable.name)
                    phi.incoming.add(incoming_var)

                    phi.variable.uninitialized |= incoming_var.uninitialized

                    # Update def-use chain
                    incoming_var.cf_references.append(phi)

    def rename_assignments(self, block):
        lastvars = dict(block.symtab)
        for stat in block.stats:
            if (isinstance(stat, NameAssignment) and
                    stat.assignment_node and
                    stat.entry.renameable):
                # print "setting", stat.lhs, hex(id(stat.lhs))
                stat.lhs.variable = block.symtab.rename(stat.entry, block)
                stat.lhs.variable.name_assignment = stat
            elif isinstance(stat, NameReference) and stat.entry.renameable:
                current_var = block.symtab.lookup_most_recent(stat.entry.name)
                stat.node.variable = current_var
                current_var.cf_references.append(stat.node)


class FuncDefExprNode(nodes.Node):
    """
    Wraps an inner function node until the closure code kicks in.
    """

    _fields = ['func_def']

class ControlFlowAnalysis(visitors.NumbaTransformer):
    """
    Control flow analysis pass that builds the CFG and injects the blocks
    into the AST (but not all blocks are injected).

    The CFG must be build in topological DFS order, e.g. the 'if' condition
    block must precede the clauses and the clauses must precede the exit.
    """

    graphviz = False
    gv_ctx = None
    source_descr = None

    function_level = 0

    def __init__(self, context, func, ast, allow_rebind_args, env, **kwargs):
        super(ControlFlowAnalysis, self).__init__(context, func, ast, env=env,
                                                  **kwargs)
        self.visitchildren = self.generic_visit
        self.current_directives = kwargs.get('directives', None) or {}
        self.current_directives['warn'] = kwargs.get('warn', True)
        self.set_default_directives()
        self.symtab = self.initialize_symtab(allow_rebind_args)

        self.graphviz = self.current_directives['control_flow.dot_output']
        if self.graphviz:
            self.gv_ctx = graphviz.GVContext()
            self.source_descr = reporting.SourceDescr(func, ast)

        # Stack of control flow blocks
        self.stack = []

        flow = ControlFlow(self.env, self.source_descr)
        self.env.translation.crnt.flow = flow
        self.flow = flow

        # TODO: Use the message collection from the environment
        # messages = reporting.MessageCollection()
        messages = env.crnt.error_env.collection
        self.warner = reaching.CFWarner(messages, self.current_directives)

        if env:
            if hasattr(env, 'translation'):
                env.translation.crnt.cfg_transform = self

    def set_default_directives(self):
        "Set some defaults for warnings"
        warn = self.current_directives['warn']
        self.current_directives.setdefault('warn.maybe_uninitialized', warn)
        self.current_directives.setdefault('warn.unused_result', False)
        self.current_directives.setdefault('warn.unused', warn)
        self.current_directives.setdefault('warn.unused_arg', warn)
        self.current_directives.setdefault('control_flow.dot_output', dot_output_graph)
        self.current_directives.setdefault('control_flow.dot_annotate_defs', False)

    def initialize_symtab(self, allow_rebind_args):
        """
        Populate the symbol table with variables and set their renaming status.

        Variables appearing in locals, or arguments typed through the 'jit'
        decorator are not renameable.
        """
        symbols = symtab.Symtab(self.symtab)
        for var_name in self.local_names:
            variable = symtab.Variable(None, name=var_name, is_local=True)

            # Set cellvar status. Free variables are not assignments, and
            # are caught in the type inferencer
            variable.is_cellvar = var_name in self.cellvars
            # variable.is_freevar = var_name in self.freevars

            variable.renameable = (
                var_name not in self.locals and not
                (variable.is_cellvar or variable.is_freevar) and
                (var_name not in self.argnames or allow_rebind_args))

            symbols[var_name] = variable

        return symbols

    def visit(self, node):
        if hasattr(node, 'lineno'):
            self.mark_position(node)

        if not self.flow.block:
            # Unreachable code
            # NOTE: removing this here means there is no validation of the
            # unreachable code!
            self.warner.warn_unreachable(node)
            return None
        return super(ControlFlowAnalysis, self).visit(node)

    def handle_inner_function(self, node):
        "Create assignment code for inner functions and mark the assignment"
        lhs = ast.Name(node.name, ast.Store())
        ast.copy_location(lhs, node)

        rhs = FuncDefExprNode(func_def=node)
        ast.copy_location(rhs, node)

        fields = rhs._fields
        rhs._fields = []
        assmnt = ast.Assign(targets=[lhs], value=rhs)
        result = self.visit(assmnt)
        rhs._fields = fields

        return result

    def visit_FunctionDef(self, node):
        #for arg in node.args:
        #    if arg.default:
        #        self.visitchildren(arg)
        if self.function_level:
            return self.handle_inner_function(node)

        self.function_level += 1

        self.visitlist(node.decorator_list)
        self.stack.append(self.flow)

        # Collect all entries
        for var_name, var in self.symtab.iteritems():
            if var_name not in self.locals:
                self.flow.entries.add(var)

        self.flow.nextblock(label='entry')
        self.mark_position(node)

        # Function body block
        node.body_block = self.flow.nextblock()
        for arg in node.args.args:
            if hasattr(arg, 'id') and hasattr(arg, 'ctx'):
                self.visit_Name(arg)
            else:
                self.visit_arg(arg, node.lineno, 0)

        self.visitlist(node.body)
        self.function_level -= 1

        # Exit point
        self.flow.add_exit(self.flow.exit_point)
        if self.flow.block:
            self.flow.block.add_child(self.flow.exit_point)

        # Cleanup graph
        # self.flow.normalize()
        reaching.check_definitions(self.env, self.flow, self.warner)

        # self.render_gv(node)

        self.flow.compute_dominators()
        self.flow.compute_dominance_frontier()
        self.flow.update_for_ssa(self.ast, self.symtab)

        return node

    def render_gv(self, node):
        graphviz.render_gv(node, self.gv_ctx, self.flow, self.current_directives)

    def mark_assignment(self, lhs, rhs=None, assignment=None, warn_unused=True):
        assert self.flow.block

        if self.flow.exceptions:
            exc_descr = self.flow.exceptions[-1]
            self.flow.block.add_child(exc_descr.entry_point)
            self.flow.nextblock()

        if not rhs:
            rhs = None

        lhs = self.visit(lhs)
        name_assignment = None
        if isinstance(lhs, ast.Name):
            name_assignment = self.flow.mark_assignment(
                    lhs, rhs, self.symtab[lhs.name], assignment,
                    warn_unused=warn_unused)

        # TODO: Generate fake RHS for for iteration target variable
        elif (isinstance(lhs, ast.Attribute) and self.flow.block and
                  assignment is not None):
            self.flow.block.stats.append(AttributeAssignment(assignment))

        if self.flow.exceptions:
            exc_descr = self.flow.exceptions[-1]
            self.flow.block.add_child(exc_descr.entry_point)
            self.flow.nextblock()

        return lhs, name_assignment

    def mark_position(self, node):
        """Mark position if DOT output is enabled."""
        if self.current_directives['control_flow.dot_output']:
            self.flow.mark_position(node)

    def visit_Assign(self, node):
        node.value = self.visit(node.value)
        if len(node.targets) == 1 and isinstance(node.targets[0],
                                                 (ast.Tuple, ast.List)):
            node.targets = node.targets[0].elts

        for i, target in enumerate(node.targets):
            # target = self.visit(target)

            maybe_unused_node = isinstance(target, nodes.MaybeUnusedNode)
            if maybe_unused_node:
                target = target.name_node

            lhs, name_assignment = self.mark_assignment(target, node.value,
                                                        assignment=node,
                                                        warn_unused=not maybe_unused_node)
            node.targets[i] = lhs
            # print "mark assignment", self.flow.block, lhs

        return node

    def visit_AugAssign(self, node):
        """
        Inplace assignment.

        Resolve a += b to a = a + b. Set 'inplace_op' attribute of the
        Assign node so later stages may recognize inplace assignment.

        Do this now, so that we can correctly mark the RHS reference.
        """
        target = node.target

        rhs_target = copy.deepcopy(target)
        rhs_target.ctx = ast.Load()
        ast.fix_missing_locations(rhs_target)

        bin_op = ast.BinOp(rhs_target, node.op, node.value)
        assignment = ast.Assign([target], bin_op)
        assignment.inplace_op = node.op
        return self.visit(assignment)

    def visit_arg(self, old_node, lineno, col_offset):
        node = nodes.Name(old_node.arg, ast.Param())
        node.lineno = lineno
        node.col_offset = col_offset
        return self._visit_Name(node)

    def visit_Name(self, old_node):
        node = nodes.Name(old_node.id, old_node.ctx)
        ast.copy_location(node, old_node)
        return self._visit_Name(node)

    def _visit_Name(self, node):
        # Set some defaults
        node.cf_maybe_null = True
        node.cf_is_null = False
        node.allow_null = False

        node.name = node.id
        if isinstance(node.ctx, ast.Param):
            var = self.symtab[node.name]
            var.is_arg = True
            self.flow.mark_assignment(node, None, var, assignment=None)
        elif isinstance(node.ctx, ast.Load):
            var = self.symtab.lookup(node.name)
            if var:
                # Local variable
                self.flow.mark_reference(node, var)

        # Set position of assignment of this definition
        if isinstance(node.ctx, (ast.Param, ast.Store)):
            var = self.symtab[node.name]
            if var.lineno == -1:
                var.lineno = getattr(node, "lineno", 0)
                var.col_offset = getattr(node, "col_offset", 0)

        return node

    def visit_MaybeUnusedNode(self, node):
        self.symtab[node.name_node.id].warn_unused = False
        return self.visit(node.name_node)

    def visit_Suite(self, node):
        if self.flow.block:
            for i, stat in enumerate(node.body):
                node.body[i] = self.visit(stat)
                if not self.flow.block:
                    stat.is_terminator = True
                    break

        return node

    def visit_ImportFrom(self, node):
        for name, target in node.names:
            if name != "*":
                self.mark_assignment(target, assignment=node)

        self.visitchildren(node)
        return node

    def exit_block(self, exit_block, node):
        node.exit_block = exit_block
        self.flow.add_exit(exit_block)
        if exit_block.parents:
            self.flow.block = exit_block
        else:
            self.flow.block = None

        return node

    def visit_If(self, node):
        exit_block = self.flow.exit_block(label='exit_if', pos=node)

        # Condition
        cond_block = self.flow.nextblock(self.flow.block, label='if_cond',
                                         is_expr=True, pos=node.test)
        node.test = self.visit(node.test)

        # Body
        if_block = self.flow.nextblock(label='if_body', pos=node.body[0])
        self.visitlist(node.body)
        if self.flow.block:
            self.flow.block.add_child(exit_block)

        # Else clause
        if node.orelse:
            else_block = self.flow.nextblock(cond_block,
                                             label='else_body',
                                             pos=node.orelse[0])
            self.visitlist(node.orelse)
            if self.flow.block:
                self.flow.block.add_child(exit_block)
        else:
            cond_block.add_child(exit_block)
            else_block = None

        node = nodes.build_if(cond_block=cond_block, test=node.test,
                              if_block=if_block, body=node.body,
                              else_block=else_block, orelse=node.orelse,
                              exit_block=exit_block)
        return self.exit_block(exit_block, node)

    def _visit_loop_body(self, node, if_block=None, is_for=None):
        """
        Visit body of while and for loops and handle 'else' clause
        """
        loop_name = "for" if is_for else "while"
        if if_block:
           node.if_block = if_block
        else:
            node.if_block = self.flow.nextblock(label="%s_body" % loop_name,
                                                pos=node.body[0])
        self.visitlist(node.body)
        self.flow.loops.pop()

        if self.flow.block:
            # Add back-edge
            self.flow.block.add_child(node.cond_block)

        # Else clause
        if node.orelse:
            node.else_block = self.flow.nextblock(
                        parent=node.cond_block,
                        label="else_clause_%s" % loop_name,
                        pos=node.orelse[0])
            self.visitlist(node.orelse)
            if self.flow.block:
                self.flow.block.add_child(node.exit_block)
        else:
            node.cond_block.add_child(node.exit_block)

        self.exit_block(node.exit_block, node)

    def visit_While(self, node):
        node.cond_block = self.flow.nextblock(label='while_condition',
                                              pos=node.test)
        node.exit_block = self.flow.exit_block(label='exit_while', pos=node)

        # Condition block
        self.flow.loops.append(LoopDescr(node.exit_block, node.cond_block))
        node.test = self.visit(node.test)

        self._visit_loop_body(node)
        return nodes.build_while(**vars(node))

    def visit_For(self, node):
        # Evaluate iterator in previous block
        node.iter = self.visit(node.iter)

        # Start condition block
        node.cond_block = self.flow.nextblock(label='for_condition',
                                              pos=node.iter)
        node.exit_block = self.flow.exit_block(label='exit_for', pos=node)

        self.flow.loops.append(LoopDescr(node.exit_block, node.cond_block))

        # Target assignment
        if_block = self.flow.nextblock(label='loop_body', pos=node.body[0])
        #node.target_block = self.flow.nextblock(label='for_target',
        #                                        pos=node.target)
        node.target, name_assignment = self.mark_assignment(
                    node.target, assignment=None, warn_unused=False)
        self._visit_loop_body(node, if_block=if_block, is_for=True)
        node = nodes.For(**vars(node))
        if name_assignment:
            name_assignment.assignment_node = node
        return node

    def visit_ListComp(self, node):
        """
        Rewrite list comprehensions to the equivalent for loops.

        AST syntax:

            ListComp(expr elt, comprehension* generators)
            comprehension = (expr target, expr iter, expr* ifs)

            'ifs' represent a chain of ANDs
        """
        assert len(node.generators) > 0

        # Create innermost body, i.e. list.append(expr)
        # TODO: size hint for PyList_New
        list_create = ast.List(elts=[], ctx=ast.Load())
        list_create.type = object_ # typesystem.list_()
        list_create = nodes.CloneableNode(list_create)
        list_value = nodes.CloneNode(list_create)
        list_append = ast.Attribute(list_value, "append", ast.Load())
        append_call = ast.Call(func=list_append, args=[node.elt],
                               keywords=[], starargs=None, kwargs=None)

        # Build up the loops from inwards to outwards
        body = append_call
        for comprehension in reversed(node.generators):
            # Hanlde the 'if' clause
            ifs = comprehension.ifs
            if len(ifs) > 1:
                make_boolop = lambda op1_op2: ast.BoolOp(op=ast.And(),
                                                         values=op1_op2)
                if_test = reduce(make_boolop, ifs)
            elif len(ifs) == 1:
                if_test, = ifs
            else:
                if_test = None

            if if_test is not None:
                body = ast.If(test=if_test, body=[body], orelse=[])

            # Wrap list.append() call or inner loops
            body = ast.For(target=comprehension.target,
                           iter=comprehension.iter, body=[body], orelse=[])

        expr = nodes.ExpressionNode(stmts=[list_create, body], expr=list_value)
        return self.visit(expr)

    def visit_GeneratorExp(self, node):
        raise error.NumbaError(
                node, "Generator comprehensions are not yet supported")

    def visit_SetComp(self, node):
        raise error.NumbaError(
                node, "Set comprehensions are not yet supported")

    def visit_DictComp(self, node):
        raise error.NumbaError(
                node, "Dict comprehensions are not yet supported")

    def visit_With(self, node):
        node.context_expr = self.visit(node.context_expr)
        if node.optional_vars:
            # TODO: Mark these as assignments!
            # Note: This is current caught in validators.py !
            node.optional_vars = self.visit(node.optional_vars)

        self.visitlist(node.body)
        return node

    def visit_Raise(self, node):
        self.visitchildren(node)
        if self.flow.exceptions:
            self.flow.block.add_child(self.flow.exceptions[-1].entry_point)

        self.flow.block = None
        return node

    def visit_Return(self, node):
        self.visitchildren(node)

        for exception in self.flow.exceptions[::-1]:
            if exception.finally_enter:
                self.flow.block.add_child(exception.finally_enter)
                if exception.finally_exit:
                    exception.finally_exit.add_child(self.flow.exit_point)
                break
        else:
            if self.flow.block:
                self.flow.block.add_child(self.flow.exit_point)

        self.flow.block = None
        return node

    def visit_Break(self, node):
        if not self.flow.loops:
            #error(node.pos, "break statement not inside loop")
            return node

        loop = self.flow.loops[-1]
        for exception in loop.exceptions[::-1]:
            if exception.finally_enter:
                self.flow.block.add_child(exception.finally_enter)
                if exception.finally_exit:
                    exception.finally_exit.add_child(loop.next_block)
                break
        else:
            self.flow.block.add_child(loop.next_block)

        #self.flow.nextblock(parent=loop.next_block)
        self.flow.block = None
        return node

    def visit_Continue(self, node):
        if not self.flow.loops:
            #error(node.pos, "continue statement not inside loop")
            return node

        loop = self.flow.loops[-1]
        for exception in loop.exceptions[::-1]:
            if exception.finally_enter:
                self.flow.block.add_child(exception.finally_enter)
                if exception.finally_exit:
                    exception.finally_exit.add_child(loop.loop_block)
                break
        else:
            self.flow.block.add_child(loop.loop_block)

        self.flow.block = None
        return node

    def visit_Print(self, node):
        self.generic_visit(node)
        return node
