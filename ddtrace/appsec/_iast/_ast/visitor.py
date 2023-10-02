#!/usr/bin/env python3
from _ast import Expr
from _ast import ImportFrom
import ast
import copy
import sys
from typing import Any
from typing import List
from typing import Set

from six import iteritems

from .._metrics import _set_metric_iast_instrumented_propagation
from ..constants import DEFAULT_PATH_TRAVERSAL_FUNCTIONS
from ..constants import DEFAULT_WEAK_RANDOMNESS_FUNCTIONS


PY3 = sys.version_info[0] >= 3
PY30_37 = sys.version_info >= (3, 0, 0) and sys.version_info < (3, 8, 0)
PY38_PLUS = sys.version_info >= (3, 8, 0)
PY39_PLUS = sys.version_info >= (3, 9, 0)

CODE_TYPE_FIRST_PARTY = "first_party"
CODE_TYPE_DD = "datadog"
CODE_TYPE_SITE_PACKAGES = "site_packages"
CODE_TYPE_STDLIB = "stdlib"
TAINT_SINK_FUNCTION_REPLACEMENT = "ddtrace_taint_sinks.ast_funcion"


class AstVisitor(ast.NodeTransformer):
    def __init__(
        self,
        filename="",
        module_name="",
    ):
        # Offset caused by inserted lines. Will be adjusted in visit_Generic
        self._aspects_spec = {
            "definitions_module": "ddtrace.appsec._iast._taint_tracking.aspects",
            "alias_module": "ddtrace_aspects",
            "functions": {
                "str": "ddtrace_aspects.str_aspect",
                "bytes": "ddtrace_aspects.bytes_aspect",
                "bytearray": "ddtrace_aspects.bytearray_aspect",
                "ddtrace_iast_flask_patch": "ddtrace_aspects.empty_func",  # To avoid recursion
            },
            "stringalike_methods": {
                "decode": "ddtrace_aspects.decode_aspect",
                "join": "ddtrace_aspects.join_aspect",
                "encode": "ddtrace_aspects.encode_aspect",
                "extend": "ddtrace_aspects.bytearray_extend_aspect",
                "upper": "ddtrace_aspects.upper_aspect",
                "lower": "ddtrace_aspects.lower_aspect",
                "swapcase": "ddtrace_aspects.swapcase_aspect",
                "title": "ddtrace_aspects.title_aspect",
                "capitalize": "ddtrace_aspects.capitalize_aspect",
                "casefold": "ddtrace_aspects.casefold_aspect",
                "translate": "ddtrace_aspects.translate_aspect",
                "format": "ddtrace_aspects.format_aspect",
                "format_map": "ddtrace_aspects.format_map_aspect",
                "zfill": "ddtrace_aspects.zfill_aspect",
                "ljust": "ddtrace_aspects.ljust_aspect",
            },
            # Replacement function for indexes and ranges
            "slices": {
                "index": "ddtrace_aspects.index_aspect",
                "slice": "ddtrace_aspects.slice_aspect",
            },
            # Replacement functions for modules
            "module_functions": {
                "BytesIO": "ddtrace_aspects.stringio_aspect",
                # "StringIO": "ddtrace_aspects.stringio_aspect",
                # "format": "ddtrace_aspects.format_aspect",
                # "format_map": "ddtrace_aspects.format_map_aspect",
            },
            "operators": {
                ast.Add: "ddtrace_aspects.add_aspect",
                "FORMAT_VALUE": "ddtrace_aspects.format_value_aspect",
                ast.Mod: "ddtrace_aspects.modulo_aspect",
                "BUILD_STRING": "ddtrace_aspects.build_string_aspect",
            },
            "excluded_from_patching": {
                # Key: module being patched
                # Value: dict with more info
                "django.utils.formats": {
                    # Key: called functions that won't be patched. E.g.: for this module
                    # not a single call for format on any function will be patched.
                    #
                    # Value: function definitions. E.g.: we won't patch any Call node inside
                    # the iter_format_modules(). If we, for example, had 'foo': ('bar', 'baz')
                    # it would mean that we wouldn't patch any call to foo() done inside the
                    # bar() or baz() function definitions.
                    "format": ("",),
                    "": ("iter_format_modules",),
                },
                "django.utils.log": {
                    "": ("",),
                },
                "django.utils.html": {"": ("format_html", "format_html_join")},
            },
            # This is a set since all functions will be replaced by taint_sink_functions
            "taint_sinks": {
                "weak_randomness": DEFAULT_WEAK_RANDOMNESS_FUNCTIONS,
                "path_traversal": DEFAULT_PATH_TRAVERSAL_FUNCTIONS,
                "other": {
                    "load",
                    "run",
                    "path",
                    "exit",
                    "sleep",
                    "socket",
                },
                # These explicitly WON'T be replaced by taint_sink_function:
                "disabled": {
                    "__new__",
                    "__init__",
                    "__dir__",
                    "__repr__",
                    "super",
                },
            },
        }
        self._sinkpoints_spec = {
            "definitions_module": "ddtrace.appsec._iast.taint_sinks",
            "alias_module": "ddtrace_taint_sinks",
            "functions": {
                "open": "ddtrace_taint_sinks.open_path_traversal",
            },
        }
        self._sinkpoints_functions = self._sinkpoints_spec["functions"]
        self.ast_modified = False
        self.filename = filename
        self.module_name = module_name

        self._aspect_index = self._aspects_spec["slices"]["index"]
        self._aspect_slice = self._aspects_spec["slices"]["slice"]
        self._aspect_functions = self._aspects_spec["functions"]
        self._aspect_operators = self._aspects_spec["operators"]
        self._aspect_methods = self._aspects_spec["stringalike_methods"]
        self._aspect_modules = self._aspects_spec["module_functions"]
        self._aspect_format_value = self._aspects_spec["operators"]["FORMAT_VALUE"]
        self._aspect_build_string = self._aspects_spec["operators"]["BUILD_STRING"]
        self.excluded_functions = self._aspects_spec["excluded_from_patching"].get(self.module_name, {})

        # Sink points
        self._taint_sink_replace_any = self._merge_taint_sinks(
            self._aspects_spec["taint_sinks"]["other"],
            self._aspects_spec["taint_sinks"]["weak_randomness"],
            *[functions for module, functions in self._aspects_spec["taint_sinks"]["path_traversal"].items()],
        )
        self._taint_sink_replace_disabled = self._aspects_spec["taint_sinks"]["disabled"]

        self.dont_patch_these_functionsdefs = set()
        for _, v in iteritems(self.excluded_functions):
            if v:
                for i in v:
                    self.dont_patch_these_functionsdefs.add(i)

        # This will be enabled when we find a module and function where we avoid doing
        # replacements and enabled again on all the others
        self.replacements_disabled_for_functiondef = False

        self.codetype = CODE_TYPE_FIRST_PARTY
        if "ast/tests/fixtures" in self.filename:
            self.codetype = CODE_TYPE_FIRST_PARTY
        elif "ddtrace" in self.filename and ("site-packages" in self.filename or "dist-packages" in self.filename):
            self.codetype = CODE_TYPE_DD
        elif "site-packages" in self.filename or "dist-packages" in self.filename:
            self.codetype = CODE_TYPE_SITE_PACKAGES
        elif "lib/python" in self.filename:
            self.codetype = CODE_TYPE_STDLIB

    @staticmethod
    def _merge_taint_sinks(*args_functions: Set[str]) -> Set[str]:
        merged_set = set()

        for functions in args_functions:
            merged_set.update(functions)

        return merged_set

    def _is_string_node(self, node):  # type: (Any) -> bool
        if PY30_37 and isinstance(node, ast.Bytes):
            return True

        if PY3 and (isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes, bytearray))):
            return True

        return False

    def _is_numeric_node(self, node):  # type: (Any) -> bool
        if PY30_37 and isinstance(node, ast.Num):
            return True

        if PY38_PLUS and (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))):
            return True

        return False

    def _is_node_constant_or_binop(self, node):  # type: (Any) -> bool
        return self._is_string_node(node) or self._is_numeric_node(node) or isinstance(node, ast.BinOp)

    def _is_call_excluded(self, func_name_node):  # type: (str) -> bool
        if not self.excluded_functions:
            return False
        excluded_for_caller = self.excluded_functions.get(func_name_node, tuple()) + self.excluded_functions.get(
            "", tuple()
        )
        return "" in excluded_for_caller or self._current_function_name in excluded_for_caller

    def _is_string_format_with_literals(self, call_node):
        # type: (ast.Call) -> bool
        return (
            self._is_string_node(call_node.func.value)  # type: ignore[attr-defined]
            and call_node.func.attr == "format"  # type: ignore[attr-defined]
            and all(map(self._is_node_constant_or_binop, call_node.args))
            and all(map(lambda x: self._is_node_constant_or_binop(x.value), call_node.keywords))
        )

    def _get_function_name(self, call_node, is_function):  # type: (ast.Call, bool) -> str
        if is_function:
            return call_node.func.id  # type: ignore[attr-defined]
        # If the call is to a method
        elif type(call_node.func) == ast.Name:
            return call_node.func.id

        return call_node.func.attr  # type: ignore[attr-defined]

    def _should_replace_with_taint_sink(self, call_node, is_function):  # type: (ast.Call, bool) -> bool
        function_name = self._get_function_name(call_node, is_function)

        if function_name in self._taint_sink_replace_disabled:
            return False

        return any(allowed in function_name for allowed in self._taint_sink_replace_any)

    def _add_original_function_as_arg(self, call_node, is_function):  # type: (ast.Call, bool) -> Any
        """
        Creates the arguments for the original function
        """
        function_name = self._get_function_name(call_node, is_function)
        function_name_arg = (
            self._name_node(call_node, function_name, ctx=ast.Load()) if is_function else copy.copy(call_node.func)
        )

        # Arguments for stack info change from:
        # my_function(self, *args, **kwargs)
        # to:
        # _add_original_function_as_arg(function_name=my_function, self, *args, **kwargs)
        new_args = [
            function_name_arg,
        ] + call_node.args

        return new_args

    def _node(self, type_, pos_from_node, **kwargs):
        # type: (Any, Any, Any) -> Any
        """
        Abstract some basic differences in node structure between versions
        """

        # Some nodes (like Module) dont have position
        lineno = getattr(pos_from_node, "lineno", 1)
        col_offset = getattr(pos_from_node, "col_offset", 0)

        if PY30_37:
            # No end_lineno or end_pos_offset
            return type_(lineno=lineno, col_offset=col_offset, **kwargs)

        # Py38+
        end_lineno = getattr(pos_from_node, "end_lineno", 1)
        end_col_offset = getattr(pos_from_node, "end_col_offset", 0)

        return type_(
            lineno=lineno, end_lineno=end_lineno, col_offset=col_offset, end_col_offset=end_col_offset, **kwargs
        )

    def _name_node(self, from_node, _id, ctx=ast.Load()):  # noqa: B008
        # type: (Any, str, Any) -> ast.Name
        return self._node(
            ast.Name,
            from_node,
            id=_id,
            ctx=ctx,
        )

    def _attr_node(self, from_node, attr, ctx=ast.Load()):  # noqa: B008
        # type: (Any, str, Any) -> ast.Name
        attr_attr = ""
        name_attr = ""
        if attr:
            aspect_split = attr.split(".")
            if len(aspect_split) > 1:
                attr_attr = aspect_split[1]
                name_attr = aspect_split[0]

        name_node = self._name_node(from_node, name_attr, ctx=ctx)
        return self._node(ast.Attribute, from_node, attr=attr_attr, ctx=ctx, value=name_node)

    def _assign_node(self, from_node, targets, value):  # type: (Any, List[Any], Any) -> Any
        return self._node(
            ast.Assign,
            from_node,
            targets=targets,
            value=value,
            type_comment=None,
        )

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

    def _none_constant(self, from_node, ctx=ast.Load()):  # noqa: B008
        # type: (Any, Any) -> Any
        if PY30_37:
            return ast.NameConstant(lineno=from_node.lineno, col_offset=from_node.col_offset, value=None)

        # 3.8+
        return ast.Constant(
            lineno=from_node.lineno,
            col_offset=from_node.col_offset,
            end_lineno=from_node.end_lineno,
            end_col_offset=from_node.end_col_offset,
            value=None,
            kind=None,
        )

    def _call_node(self, from_node, func, args):  # type: (Any, Any, List[Any]) -> Any
        return self._node(ast.Call, from_node, func=func, args=args, keywords=[])

    def visit_Module(self, module_node):
        # type: (ast.Module) -> Any
        """
        Insert the import statement for the replacements module
        """
        insert_position = self.find_insert_position(module_node)

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

        definitions_module = self._sinkpoints_spec["definitions_module"]
        replacements_import = self._node(
            ast.Import,
            module_node,
            names=[
                ast.alias(
                    lineno=1,
                    col_offset=0,
                    name=definitions_module,
                    asname=self._sinkpoints_spec["alias_module"],
                )
            ],
        )
        module_node.body.insert(insert_position, replacements_import)
        # Must be called here instead of the start so the line offset is already
        # processed
        self.generic_visit(module_node)
        return module_node

    def visit_FunctionDef(self, def_node):
        # type: (ast.FunctionDef) -> Any
        """
        Special case for some tests which would enter in a patching
        loop otherwise when visiting the check functions
        """
        self.replacements_disabled_for_functiondef = def_node.name in self.dont_patch_these_functionsdefs

        self.generic_visit(def_node)
        self._current_function_name = None

        return def_node

    def visit_Call(self, call_node):  # type: (ast.Call) -> Any
        """
        Replace a call or method
        """
        self.generic_visit(call_node)
        func_member = call_node.func
        call_modified = False
        if self.replacements_disabled_for_functiondef:
            return call_node

        if isinstance(func_member, ast.Name) and func_member.id:
            # Normal function call with func=Name(...), just change the name
            func_name_node = func_member.id
            aspect = self._aspect_functions.get(func_name_node)
            if aspect:
                # Insert original function name as first parameter
                call_node.args = self._add_original_function_as_arg(call_node, True)
                # Substitute function call
                call_node.func = self._attr_node(call_node, aspect)
                self.ast_modified = call_modified = True
            else:
                sink_point = self._sinkpoints_functions.get(func_name_node)
                if sink_point:
                    call_node.func = self._attr_node(call_node, sink_point)
                    self.ast_modified = call_modified = True
        # Call [attr] -> Attribute [value]-> Attribute [value]-> Attribute
        # a.b.c.method()
        # replaced_method(a.b.c)
        elif isinstance(func_member, ast.Attribute):
            # Method call:
            method_name = func_member.attr

            if self._is_call_excluded(method_name):
                # Early return if method is excluded
                return call_node

            if self._is_string_format_with_literals(call_node):
                return call_node

            aspect = self._aspect_methods.get(method_name)

            if aspect:
                # Move the Attribute.value to 'args'
                new_arg = func_member.value
                call_node.args.insert(0, new_arg)

                # Insert original method as first parameter (a.b.c.method)
                call_node.args = self._add_original_function_as_arg(call_node, False)

                # Create a new Name node for the replacement and set it as node.func
                call_node.func = self._attr_node(call_node, aspect)
                self.ast_modified = call_modified = True

            elif hasattr(func_member.value, "id") or hasattr(func_member.value, "attr"):
                aspect = self._aspect_modules.get(method_name, None)
                if aspect:
                    # Move the Function to 'args'
                    call_node.args.insert(0, call_node.func)

                    # Create a new Name node for the replacement and set it as node.func
                    call_node.func = self._attr_node(call_node, aspect)
                    self.ast_modified = call_modified = True

        if self.codetype == CODE_TYPE_FIRST_PARTY:
            # Function replacement case
            if isinstance(call_node.func, ast.Name):
                aspect = self._should_replace_with_taint_sink(call_node, True)
                if aspect:
                    call_node.args = self._add_original_function_as_arg(call_node, False)
                    call_node.func = self._attr_node(call_node, TAINT_SINK_FUNCTION_REPLACEMENT)
                    self.ast_modified = call_modified = True

            # Method replacement case
            elif isinstance(call_node.func, ast.Attribute):
                aspect = self._should_replace_with_taint_sink(call_node, False)
                if aspect:
                    # Create a new Name node for the replacement and set it as node.func
                    call_node.args = self._add_original_function_as_arg(call_node, False)
                    call_node.func = self._attr_node(call_node, TAINT_SINK_FUNCTION_REPLACEMENT)
                    self.ast_modified = call_modified = True

        if call_modified:
            _set_metric_iast_instrumented_propagation()

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
            _set_metric_iast_instrumented_propagation()

            return ast.Call(self._attr_node(call_node, aspect), [call_node.left, call_node.right], [])

        return call_node

    def visit_FormattedValue(self, fmt_value_node):  # type: (ast.FormattedValue) -> Any
        """
        Visit a FormattedValue node which are the constituent atoms for the
        JoinedStr which are used to implement f-strings.
        """

        self.generic_visit(fmt_value_node)

        if hasattr(fmt_value_node, "value") and self._is_node_constant_or_binop(fmt_value_node.value):
            return fmt_value_node

        func_name_node = self._attr_node(fmt_value_node, self._aspect_format_value)

        options_int = self._node(
            ast.Constant,
            fmt_value_node,
            value=fmt_value_node.conversion,
            kind=None,
        )

        format_spec = fmt_value_node.format_spec if fmt_value_node.format_spec else self._none_constant(fmt_value_node)
        call_node = self._call_node(
            fmt_value_node,
            func=func_name_node,
            args=[fmt_value_node.value, options_int, format_spec],
        )

        self.ast_modified = True
        _set_metric_iast_instrumented_propagation()
        return call_node

    def visit_JoinedStr(self, joinedstr_node):  # type: (ast.JoinedStr) -> Any
        """
        Replaced the JoinedStr AST node with a Call to the replacement function. Most of
        the work inside fstring is done by visit_FormattedValue above.
        """
        self.generic_visit(joinedstr_node)

        if all(
            map(
                lambda x: isinstance(x, ast.FormattedValue) or self._is_node_constant_or_binop(x),
                joinedstr_node.values,
            )
        ):
            return joinedstr_node

        func_name_node = self._attr_node(
            joinedstr_node,
            self._aspect_build_string,
            ctx=ast.Load(),
        )
        call_node = self._call_node(
            joinedstr_node,
            func=func_name_node,
            args=joinedstr_node.values,
        )

        self.ast_modified = True
        _set_metric_iast_instrumented_propagation()
        return call_node

    def visit_AugAssign(self, augassign_node):  # type: (ast.AugAssign) -> Any
        """Replace an inplace add or multiply."""
        if isinstance(augassign_node.target, ast.Subscript):
            # Can't augassign to function call, ignore this node
            augassign_node.target.avoid_convert = True  # type: ignore[attr-defined]
            self.generic_visit(augassign_node)
            return augassign_node

        # TODO: Replace an inplace add or multiply (+= / *=)
        return augassign_node

    def visit_Assign(self, assign_node):  # type: (ast.Assign) -> Any
        """
        Decompose multiple assignment into single ones and
        check if any item in the targets list is if type Subscript and if
        that's the case further decompose it to use a temp variable to
        avoid assigning to a function call.
        """
        # a = b = c
        # __dd_tmp = c
        # a = __dd_tmp

        ret_nodes = []

        if len(assign_node.targets) > 1:
            # Multiple assignments, assign the value to a temporal variable
            tmp_var_left = self._name_node(assign_node, "__dd_tmp", ctx=ast.Store())
            assign_value = self._name_node(assign_node, "__dd_tmp", ctx=ast.Load())
            assign_to_tmp = self._assign_node(from_node=assign_node, targets=[tmp_var_left], value=assign_node.value)
            ret_nodes.append(assign_to_tmp)
            self.ast_modified = True
        else:
            assign_value = assign_node.value  # type: ignore

        for target in assign_node.targets:
            if isinstance(target, ast.Subscript):
                # We can't assign to a function call, which is anyway going to rewrite
                # the index destination so we just ignore that target
                target.avoid_convert = True  # type: ignore[attr-defined]
            elif isinstance(target, (List, ast.Tuple)):
                # Same for lists/tuples on the left side of the assignment
                for element in target.elts:
                    if isinstance(element, ast.Subscript):
                        element.avoid_convert = True  # type: ignore[attr-defined]

            # Create a normal assignment. This way we decompose multiple assignments
            # like (a = b = c) into a = b and a = c so the transformation above
            # is possible.
            # Decompose it into a normal, not multiple, assignment
            new_assign_value = copy.copy(assign_value)

            new_target = copy.copy(target)

            single_assign = self._assign_node(assign_node, [new_target], new_assign_value)

            self.generic_visit(single_assign)
            ret_nodes.append(single_assign)

        if len(ret_nodes) == 1:
            return ret_nodes[0]

        return ret_nodes

    def visit_Delete(self, assign_node):  # type: (ast.Delete) -> Any
        # del replaced_index(foo, bar) would fail so avoid converting the right hand side
        # since it's going to be deleted anyway

        for target in assign_node.targets:
            if isinstance(target, ast.Subscript):
                target.avoid_convert = True  # type: ignore[attr-defined]

        self.generic_visit(assign_node)
        return assign_node

    def visit_Subscript(self, subscr_node):  # type: (ast.Subscript) -> Any
        """
        Turn an indexes[1] and slices[0:1:2] into the replacement function call
        Optimization: dont convert if the indexes are strings
        """
        self.generic_visit(subscr_node)

        # We mark nodes to avoid_convert (check visit_Delete, visit_AugAssign, visit_Assign) due to complex
        # expressions that raise errors when try to replace with index aspects
        if hasattr(subscr_node, "avoid_convert"):
            return subscr_node

        # Optimization: String literal slices and indexes are not patched
        if self._is_string_node(subscr_node.value):
            return subscr_node

        attr_node = self._attr_node(subscr_node, "")

        call_node = self._call_node(
            subscr_node,
            func=attr_node,
            args=[],
        )
        if isinstance(subscr_node.slice, ast.Slice):
            # Slice[0:1:2]. The other cases in this if are Indexes[0]
            aspect_split = self._aspect_slice.split(".")
            call_node.func.attr = aspect_split[1]
            call_node.func.value.id = aspect_split[0]
            none_node = self._none_constant(subscr_node)
            lower = none_node if subscr_node.slice.lower is None else subscr_node.slice.lower
            upper = none_node if subscr_node.slice.upper is None else subscr_node.slice.upper
            step = none_node if subscr_node.slice.step is None else subscr_node.slice.step
            call_node.args.extend([subscr_node.value, lower, upper, step])
            self.ast_modified = True
        elif PY39_PLUS:
            if self._is_string_node(subscr_node.slice):
                return subscr_node
            # In Py39+ the if subscr_node.slice member is not a Slice, is directly an unwrapped value
            # for the index (e.g. Constant for a number, Name for a var, etc)
            aspect_split = self._aspect_index.split(".")
            call_node.func.attr = aspect_split[1]
            call_node.func.value.id = aspect_split[0]
            call_node.args.extend([subscr_node.value, subscr_node.slice])
        # TODO: python 3.8 isn't working correctly with index_aspect, tests raise:
        #  corrupted size vs. prev_size in fastbins
        #  Test failed with exit code -6
        #  https://app.circleci.com/pipelines/github/DataDog/dd-trace-py/46665/workflows/3cf1257c-feaf-4653-bb9c-fb840baa1776/jobs/3031799
        # elif isinstance(subscr_node.slice, ast.Index):
        #     if self._is_string_node(subscr_node.slice.value):  # type: ignore[attr-defined]
        #         return subscr_node
        #     aspect_split = self._aspect_index.split(".")
        #     call_node.func.attr = aspect_split[1]
        #     call_node.func.value.id = aspect_split[0]
        #     call_node.args.extend([subscr_node.value, subscr_node.slice.value])  # type: ignore[attr-defined]
        else:
            return subscr_node

        self.ast_modified = True
        return call_node
