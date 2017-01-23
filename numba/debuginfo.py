"""
Implements helpers to build LLVM debuginfo.
"""

from __future__ import absolute_import

import abc
import os.path

from llvmlite import ir


class AbstractDIBuilder(abc.ABC):
    @abc.abstractmethod
    def mark_location(self, builder, loc):
        """Emit source location information to the given IRBuilder.
        """
        pass

    @abc.abstractmethod
    def mark_subprogram(self, function, name, line):
        """Emit source location information for the given function.
        """
        pass

    @abc.abstractmethod
    def finalize(self):
        """Finalize the debuginfo by emitting all necessary metadata.
        """
        pass


class DummyDIBuilder(AbstractDIBuilder):

    def __init__(self, module, filepath):
        pass

    def mark_location(self, builder, loc):
        pass

    def mark_subprogram(self, function, name, line):
        pass

    def finalize(self):
        pass


class DIBuilder(AbstractDIBuilder):
    DWARF_VERSION = 4
    DEBUG_INFO_VERSION = 3
    DBG_CU_NAME = 'llvm.dbg.cu'

    def __init__(self, module, filepath):
        self.module = module
        self.filepath = os.path.abspath(filepath)
        self.difile = self._di_file()
        self.subprograms = []
        self.dicompileunit = self._di_compile_unit()

    def mark_location(self, builder, loc):
        builder.debug_metadata = self._add_location(loc.line)

    def mark_subprogram(self, function, name, line):
        di_subp = self._add_subprogram(name=name, linkagename=function.name,
                                       line=line)
        function.set_metadata("dbg", di_subp)

    def finalize(self):
        dbgcu = self.module.get_or_insert_named_metadata(self.DBG_CU_NAME)
        dbgcu.add(self.dicompileunit)
        self._set_module_flags()

    #
    # Internal APIs
    #

    def _set_module_flags(self):
        """Set the module flags metadata
        """
        module = self.module
        mflags = module.get_or_insert_named_metadata('llvm.module.flags')
        if self.DWARF_VERSION is not None:
            dwarf_version = module.add_metadata([
                self._const_int(2),
                "Dwarf Version",
                self._const_int(self.DWARF_VERSION)
            ])
            if dwarf_version not in mflags.operands:
                mflags.add(dwarf_version)
        debuginfo_version = module.add_metadata([
            self._const_int(2),
            "Debug Info Version",
            self._const_int(self.DEBUG_INFO_VERSION)
        ])
        if debuginfo_version not in mflags.operands:
            mflags.add(debuginfo_version)

    def _add_subprogram(self, name, linkagename, line):
        """Emit subprogram metdata
        """
        subp = self._di_subprogram(name, linkagename, line)
        self.subprograms.append(subp)
        return subp

    def _add_location(self, line):
        """Emit location metatdaa
        """
        loc = self._di_location(line)
        return loc

    @classmethod
    def _const_int(cls, num, bits=32):
        """Util to create constant int in metadata
        """
        return ir.IntType(bits)(num)

    @classmethod
    def _const_bool(cls, boolean):
        """Util to create constant boolean in metadata
        """
        return ir.IntType(1)(boolean)

    #
    # Helpers to emit the metadata nodes
    #

    def _di_file(self):
        return self.module.add_debug_info('DIFile', {
            'directory': os.path.dirname(self.filepath),
            'filename': os.path.basename(self.filepath),
        })

    def _di_compile_unit(self):
        return self.module.add_debug_info('DICompileUnit', {
            'language': ir.DIToken('DW_LANG_Python'),
            'file': self.difile,
            'producer': 'Numba',
            'runtimeVersion': 0,
            'isOptimized': True,
            'emissionKind': 1,  # 0-NoDebug, 1-FullDebug
        }, is_distinct=True)

    def _di_subroutine_type(self):
        return self.module.add_debug_info('DISubroutineType', {
            'types': self.module.add_metadata([]),
        })

    def _di_subprogram(self, name, linkagename, line):
        return self.module.add_debug_info('DISubprogram', {
            'name': name,
            'linkageName': linkagename,
            'scope': self.difile,
            'file': self.difile,
            'line': line,
            'type': self._di_subroutine_type(),
            'isLocal': False,
            'isDefinition': True,
            'scopeLine': line,
            'isOptimized': True,
            'variables': self.module.add_metadata([]),
            'unit': self.dicompileunit,
        }, is_distinct=True)

    def _di_location(self, line):
        return self.module.add_debug_info('DILocation', {
            'line': line,
            'column': 1,
            'scope': self.subprograms[-1],
        })


class NvvmDIBuilder(DIBuilder):
    """
    Only implemented the minimal metadata to get line number information.
    See http://llvm.org/releases/3.4/docs/LangRef.html
    """
    # These constants are copied from llvm3.4
    DW_LANG_Python = 0x0014
    DI_Compile_unit = 786449
    DI_Subroutine_type = 786453
    DI_Subprogram = 786478
    DI_File = 786473

    DWARF_VERSION = None  # don't emit DWARF version
    DEBUG_INFO_VERSION = 1  # as required by NVVM IR Spec
    # Rename DIComputeUnit MD to hide it from llvm.parse_assembly()
    # which strips invalid/outdated debug metadata
    DBG_CU_NAME = 'numba.llvm.dbg.cu'

    # Default member
    # Used in mark_location to remember last lineno to avoid duplication
    _last_lineno = None

    def mark_location(self, builder, loc):
        # Avoid duplication
        if self._last_lineno == loc.line:
            return
        self._last_lineno = loc.line
        # Add call to an inline asm to mark line location
        asmty = ir.FunctionType(ir.VoidType(), [])
        asm = ir.InlineAsm(asmty, "// dbg {}".format(loc.line), "",
                           side_effect=True)
        call = builder.call(asm, [])
        md = self._di_location(loc.line)
        call.set_metadata('numba.dbg', md)

    def mark_subprogram(self, function, name, line):
        self._add_subprogram(name=name, linkagename=function.name, line=line)

    #
    # Helper methods to create the metadata nodes.
    #

    def _filepair(self):
        return self.module.add_metadata([
            os.path.basename(self.filepath),
            os.path.dirname(self.filepath),
        ])

    def _di_file(self):
        return self.module.add_metadata([
            self._const_int(self.DI_File),
            self._filepair(),
        ])

    def _di_compile_unit(self):
        filepair = self._filepair()
        empty = self.module.add_metadata([self._const_int(0)])
        return self.module.add_metadata([
            self._const_int(self.DI_Compile_unit),         # tag
            filepair,                   # source directory and file pair
            self._const_int(self.DW_LANG_Python),  # language
            'Numba',                     # producer
            self._const_bool(True),      # optimized
            "",                          # flags??
            self._const_int(0),          # runtime version
            empty,                       # enums types
            empty,                       # retained types
            self.module.add_metadata(self.subprograms),  # subprograms
            empty,                       # global variables
            empty,                       # imported entities
            "",                          # split debug filename
        ])

    def _di_subroutine_type(self):
        types = self.module.add_metadata([None])
        return self.module.add_metadata([
            self._const_int(self.DI_Subroutine_type),                # tag
            self._const_int(0),
            None,
            "",
            self._const_int(0),                 # line of definition
            self._const_int(0, 64),             # size in bits
            self._const_int(0, 64),             # offset in bits
            self._const_int(0, 64),             # align in bits
            self._const_int(0),                 # flags
            None,
            types,
            self._const_int(0),
            None,
            None,
            None,
        ])

    def _di_subprogram(self, name, linkagename, line):
        function_ptr = self.module.get_global(linkagename)
        subroutine_type = self._di_subroutine_type()
        funcvars = self.module.add_metadata([self._const_int(0)])
        context = self._di_file()
        return self.module.add_metadata([
            self._const_int(self.DI_Subprogram),   # tag
            self._filepair(),          # source dir & file
            context,                   # context descriptor
            name,                      # name
            name,                      # display name
            linkagename,               # linkage name
            self._const_int(line),     # line
            subroutine_type,           # type descriptor
            self._const_bool(False),   # is local
            self._const_bool(True),    # is definition
            self._const_int(0),        # virtuality
            self._const_int(0),        # virtual function index
            None,                     # vtable base type
            self._const_int(0),        # flags
            self._const_bool(True),    # is optimized
            function_ptr,              # pointer to function
            None,                      # function template parameters
            None,                      # function declaration descriptor
            funcvars,                     # function variables
            self._const_int(line)      # scope line
        ])

    def _di_location(self, line):
        return self.module.add_metadata([
            self._const_int(line),   # line
            self._const_int(0),      # column
            self.subprograms[-1],    # scope
            None,                    # original scope
        ])

