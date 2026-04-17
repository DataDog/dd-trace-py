from __future__ import annotations

import ast
import builtins
import os

from . import taint


_SKIP_TYPES = (int, float, str, bool, bytes, type(None), type)

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

TAINT_MARK_NAME = "__taint_mark__"


def _is_internal(filename: str) -> bool:
    if filename.startswith(_PLUGIN_DIR):
        return True
    return "site-packages" in filename


def taint_mark(obj: object) -> None:
    if obj is not None and not isinstance(obj, _SKIP_TYPES):
        taint.taint(obj)


def _target_to_load(target: ast.AST) -> ast.expr | None:
    """Convert a Store-context target node to a Load-context expression."""
    if isinstance(target, ast.Name):
        return ast.Name(id=target.id, ctx=ast.Load())
    if isinstance(target, ast.Subscript):
        return ast.Subscript(value=target.value, slice=target.slice, ctx=ast.Load())
    if isinstance(target, ast.Attribute):
        return ast.Attribute(value=target.value, attr=target.attr, ctx=ast.Load())
    if isinstance(target, ast.Starred):
        return _target_to_load(target.value)
    return None


class TaintTransformer(ast.NodeTransformer):
    def _make_taint_call(self, expr: ast.expr, node: ast.AST) -> ast.Expr:
        call = ast.Expr(
            value=ast.Call(
                func=ast.Name(id=TAINT_MARK_NAME, ctx=ast.Load()),
                args=[expr],
                keywords=[],
            )
        )
        ast.copy_location(call, node)
        ast.copy_location(call.value, node)
        # Ensure end_lineno matches lineno for injected single-line nodes
        for n in ast.walk(call):
            if hasattr(n, "lineno"):
                setattr(n, "end_lineno", getattr(n, "end_lineno", None) or n.lineno)
                setattr(n, "end_col_offset", getattr(n, "end_col_offset", None) or getattr(n, "col_offset", 0))
        return call

    def _taint_calls_for_target(self, target: ast.AST, node: ast.AST) -> list[ast.Expr]:
        """Generate taint calls for an assignment target."""
        if isinstance(target, (ast.Tuple, ast.List)):
            calls = []
            for elt in target.elts:
                calls.extend(self._taint_calls_for_target(elt, node))
            return calls
        load_expr = _target_to_load(target)
        if load_expr is not None:
            return [self._make_taint_call(load_expr, node)]
        return []

    def visit_Assign(self, node: ast.Assign) -> list[ast.AST]:
        self.generic_visit(node)
        result: list[ast.AST] = [node]
        for target in node.targets:
            result.extend(self._taint_calls_for_target(target, node))
        return result

    def visit_AnnAssign(self, node: ast.AnnAssign) -> list[ast.AST]:
        self.generic_visit(node)
        if node.value is None:
            return [node]
        result: list[ast.AST] = [node]
        result.extend(self._taint_calls_for_target(node.target, node))
        return result

    def visit_AugAssign(self, node: ast.AugAssign) -> list[ast.AST]:
        self.generic_visit(node)
        result: list[ast.AST] = [node]
        result.extend(self._taint_calls_for_target(node.target, node))
        return result

    def visit_For(self, node: ast.For) -> ast.For:
        self.generic_visit(node)
        taint_calls = self._taint_calls_for_target(node.target, node)
        node.body = taint_calls + node.body
        return node

    def visit_NamedExpr(self, node: ast.NamedExpr) -> ast.expr:
        self.generic_visit(node)
        taint_call = ast.Call(
            func=ast.Name(id=TAINT_MARK_NAME, ctx=ast.Load()),
            args=[ast.Name(id=node.target.id, ctx=ast.Load())],
            keywords=[],
        )
        wrapper = ast.Subscript(
            value=ast.Tuple(elts=[node, taint_call], ctx=ast.Load()),
            slice=ast.Constant(value=0),
            ctx=ast.Load(),
        )
        return ast.copy_location(wrapper, node)

    def visit_With(self, node: ast.With) -> ast.With:
        self.generic_visit(node)
        taint_calls = []
        for item in node.items:
            if item.optional_vars is not None:
                taint_calls.extend(self._taint_calls_for_target(item.optional_vars, node))
        node.body = taint_calls + node.body
        return node


_FALLBACK_AST = ast.parse(
    "import builtins as __taint_builtins__\n"
    "__taint_mark__ = getattr(__taint_builtins__, '__taint_mark__', lambda _: None)\n"
).body


def _inject_fallback(tree: ast.Module) -> None:
    """Insert a no-op __taint_mark__ definition at the top of the module.

    This ensures cached .pyc files work in subprocesses (e.g. unittest)
    that don't load the pytest plugin / set builtins.__taint_mark__.
    """
    import copy

    for i, node in enumerate(copy.deepcopy(_FALLBACK_AST)):
        tree.body.insert(i, node)


def _fix_locations(tree: ast.AST) -> None:
    """Ensure all AST nodes have valid, monotonically non-decreasing line numbers.

    CPython 3.12+ rejects ASTs where a child has a smaller line number than
    a preceding sibling. After our transformer inserts new nodes with
    copy_location, the ordering can break -- especially when we transform an
    AST that pytest's assertion rewriter already modified.
    """
    ast.fix_missing_locations(tree)
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody", "handlers"):
            stmts = getattr(node, field, None)
            if not isinstance(stmts, list):
                continue
            prev_line = 0
            for stmt in stmts:
                for child in ast.walk(stmt):
                    if hasattr(child, "lineno"):
                        if child.lineno < prev_line:
                            child.lineno = prev_line
                        end_lineno = getattr(child, "end_lineno", None)
                        if end_lineno is None or end_lineno < child.lineno:
                            setattr(child, "end_lineno", child.lineno)
                        if hasattr(child, "col_offset") and hasattr(child, "end_col_offset"):
                            end_col = getattr(child, "end_col_offset", None)
                            if (
                                end_col is not None
                                and end_col < child.col_offset
                                and child.lineno == getattr(child, "end_lineno", None)
                            ):
                                setattr(child, "end_col_offset", child.col_offset)
                if hasattr(stmt, "lineno"):
                    prev_line = max(prev_line, getattr(stmt, "end_lineno", None) or stmt.lineno)


_original_compile = None
_test_paths: list[str] = []
_transformer = TaintTransformer()
_compiling = False


def _should_rewrite(filename: str) -> bool:
    if not isinstance(filename, str) or not filename.endswith(".py"):
        return False
    resolved = os.path.realpath(filename)
    if _is_internal(resolved):
        return False
    return any(resolved.startswith(tp) for tp in _test_paths)


def _patched_compile(source, filename, mode, *args, **kwargs):
    global _compiling
    if not _compiling and mode == "exec" and _should_rewrite(filename):
        _compiling = True
        try:
            if isinstance(source, (str, bytes)):
                if isinstance(source, bytes):
                    source = source.decode("utf-8")
                tree = ast.parse(source, filename=filename)
            elif isinstance(source, ast.AST):
                tree = source
            else:
                return _original_compile(source, filename, mode, *args, **kwargs)
            tree = _transformer.visit(tree)
            # Inject a safe fallback at module top so cached .pyc files
            # work even in subprocesses that don't load the plugin:
            #   if not callable(globals().get("__taint_mark__")):
            #       def __taint_mark__(_): pass
            _inject_fallback(tree)
            _fix_locations(tree)
            return _original_compile(tree, filename, mode, *args, **kwargs)
        except SyntaxError:
            return _original_compile(source, filename, mode, *args, **kwargs)
        finally:
            _compiling = False
    return _original_compile(source, filename, mode, *args, **kwargs)


def install(test_paths: list[str] | None = None) -> None:
    global _original_compile, _test_paths
    if _original_compile is not None:
        return
    if test_paths is None:
        test_paths = [os.getcwd()]
    _test_paths = [os.path.realpath(p) for p in test_paths]

    # Make taint_mark available as a builtin — accessible from any module
    setattr(builtins, TAINT_MARK_NAME, taint_mark)

    # Patch compile to transform AST transparently
    _original_compile = builtins.compile
    builtins.compile = _patched_compile


def uninstall() -> None:
    global _original_compile
    if _original_compile is not None:
        builtins.compile = _original_compile
        _original_compile = None
    if hasattr(builtins, TAINT_MARK_NAME):
        delattr(builtins, TAINT_MARK_NAME)
