import numba
from numba import *
from numba import visitors, nodes, error, functions, transforms
from numba.typesystem import is_obj, promote_closest, promote_to_native

logger = logging.getLogger(__name__)

from numba.external import pyapi

class FunctionCallSpecializer(visitors.NumbaTransformer,
                              visitors.NoPythonContextMixin,
                              transforms.BuiltinResolverMixinBase):

    def visit_NativeCallNode(self, node):
        if is_obj(node.signature.return_type):
            if self.nopython:
                raise error.NumbaError(
                    node, "Cannot call function returning object in "
                          "nopython context")

            self.generic_visit(node)
            return nodes.ObjectTempNode(node)

        self.generic_visit(node)
        return node

    def visit_NativeFunctionCallNode(self, node):
        return self.visit_NativeCallNode(node)
