#!/usr/bin/env python3

from _ast import Expr
from _ast import ImportFrom
import ast
import sys
from typing import Any


PY27_37 = sys.version_info < (3, 8, 0)


class AstVisitor(ast.NodeTransformer):
    def __init__(
        self,
        filename="",
        module_name="",
    ):
        # Offset caused by inserted lines. Will be adjusted in visit_Generic
        self._aspects_spec = {
            "definitions_module": "ddtrace.appsec.iast._ast.aspects",
            "alias_module": "ddtrace_aspects",
            "functions": {
                "str": "ddtrace_aspects.str_aspect",
                "decode": "ddtrace_aspects.decode_aspect",
                "encode": "ddtrace_aspects.encode_aspect",
            },
            "operators": {
                ast.Add: "ddtrace_aspects.add_aspect",
            },
        }
        self._aspect_functions = self._aspects_spec["functions"]
        self._aspect_operators = self._aspects_spec["operators"]

        self.ast_modified = False
        self.filename = filename
        self.module_name = module_name

    def _node(self, type_, pos_from_node, **kwargs):  # type: (Any, Any, Any) -> Any
        """
        Abstract some basic differences in node structure between versions
        """

        # Some nodes (like Module) dont have position
        lineno = getattr(pos_from_node, "lineno", 1)
        col_offset = getattr(pos_from_node, "col_offset", 0)

        if PY27_37:
            # No end_lineno or end_pos_offset
            return type_(lineno=lineno, col_offset=col_offset, **kwargs)

        # Py38+
        end_lineno = getattr(pos_from_node, "end_lineno", 1)
        end_col_offset = getattr(pos_from_node, "end_col_offset", 0)

        return type_(
            lineno=lineno, end_lineno=end_lineno, col_offset=col_offset, end_col_offset=end_col_offset, **kwargs
        )

    def _name_node(self, from_node, _id, ctx=ast.Load()):  # type: (Any, str, Any) -> ast.Name
        return self._node(
            ast.Name,
            from_node,
            id=_id,
            ctx=ctx,
        )

    def _attr_node(self, from_node, attr, ctx=ast.Load()):  # type: (Any, str, Any) -> ast.Name
        attr_attr = ""
        name_attr = ""
        if attr:
            aspect_split = attr.split(".")
            if len(aspect_split) > 1:
                attr_attr = aspect_split[1]
                name_attr = aspect_split[0]

        name_node = self._name_node(from_node, name_attr, ctx=ctx)
        return self._node(ast.Attribute, from_node, attr=attr_attr, ctx=ctx, value=name_node)

    def find_insert_position(self, module_node):  # type: (ast.Module) -> int
        insert_position = 0
        from_future_import_found = False
        import_found = False

        # Check all nodes that are "from __future__ import...", as we must insert after them.
        #
        # Caveat:
        # - body_node.lineno doesn't work because a large docstring changes the lineno
        #   but not the position in the nodes (i.e. this can happen: lineno==52, position==2)
        # TODO: Test and implement cases with docstrings before future imports, etc.
        for body_node in module_node.body:
            insert_position += 1
            if isinstance(body_node, ImportFrom) and body_node.module == "__future__":
                import_found = True
                from_future_import_found = True
            # As soon as we start a non-futuristic import we can stop looking
            elif isinstance(body_node, ImportFrom):
                import_found = True
            elif isinstance(body_node, Expr) and not import_found:
                continue
            elif from_future_import_found:
                insert_position -= 1
                break
            else:
                break

        if not from_future_import_found:
            # No futuristic import found, reset the position to 0
            insert_position = 0

        return insert_position

    def visit_Module(self, module_node):  # type: (ast.Module) -> Any
        """
        Insert the import statement for the replacements module
        """
        insert_position = self.find_insert_position(module_node)
        assert self._aspects_spec
        definitions_module = self._aspects_spec["definitions_module"]
        replacements_import = self._node(
            ast.Import,
            module_node,
            names=[
                ast.alias(
                    lineno=1,
                    col_offset=0,
                    name=definitions_module,
                    asname=self._aspects_spec["alias_module"],
                )
            ],
        )
        module_node.body.insert(insert_position, replacements_import)
        # Must be called here instead of the start so the line offset is already
        # processed
        self.generic_visit(module_node)
        return module_node

    def visit_Call(self, call_node):  # type: (ast.Call) -> Any
        """
        Replace a call or method
        """
        self.generic_visit(call_node)
        func_member = call_node.func

        if isinstance(func_member, ast.Name) and func_member.id:
            # Normal function call with func=Name(...), just change the name
            func_name_node = func_member.id
            aspect = self._aspect_functions.get(func_name_node)
            if aspect:
                call_node.func = self._attr_node(call_node, aspect)
                self.ast_modified = True

        return call_node

    def visit_BinOp(self, call_node):  # type: (ast.BinOp) -> Any
        """
        Replace a binary operator
        """
        self.generic_visit(call_node)
        operator = call_node.op

        aspect = self._aspect_operators.get(operator.__class__)
        if aspect:
            self.ast_modified = True
            return ast.Call(self._attr_node(call_node, aspect), [call_node.left, call_node.right], [])

        return call_node
