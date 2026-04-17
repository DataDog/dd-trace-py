from __future__ import annotations

import ast
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
import types

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
        return ast.copy_location(call, node)

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
        # Walrus := can't be replaced with a statement list, so we wrap:
        # (x := expr) → (x := expr, __taint_mark__(x))[0]
        # This evaluates both but returns the original value.
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


class TaintFinder(importlib.abc.MetaPathFinder):
    def __init__(self, test_paths: list[str]) -> None:
        self._test_paths = [os.path.abspath(p) for p in test_paths]
        self._rewriting: set[str] = set()

    def _should_rewrite(self, fullname: str, path: str) -> bool:
        if path is None or not path.endswith(".py"):
            return False
        abspath = os.path.abspath(path)
        if _is_internal(abspath):
            return False
        return any(abspath.startswith(tp) for tp in self._test_paths)

    def find_spec(self, fullname, path, target=None) -> importlib.machinery.ModuleSpec | None:
        if fullname in self._rewriting:
            return None

        self._rewriting.add(fullname)
        try:
            spec = importlib.util.find_spec(fullname)
        except (ModuleNotFoundError, ValueError):
            return None
        finally:
            self._rewriting.discard(fullname)

        if spec is None or spec.origin is None:
            return None

        if not self._should_rewrite(fullname, spec.origin):
            return None

        spec.loader = TaintLoader(spec.origin)
        return spec


class TaintLoader(importlib.abc.Loader):
    def __init__(self, origin: str) -> None:
        self._origin = origin

    def create_module(self, spec):
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        with open(self._origin, "rb") as f:
            source = f.read()
        tree = ast.parse(source, filename=self._origin)
        tree = TaintTransformer().visit(tree)
        ast.fix_missing_locations(tree)
        try:
            from _pytest.assertion.rewrite import rewrite_asserts

            rewrite_asserts(tree, source)
        except ImportError:
            pass
        code = compile(tree, self._origin, "exec")
        module.__dict__[TAINT_MARK_NAME] = taint_mark
        exec(code, module.__dict__)  # nosec B102


_finder: TaintFinder | None = None


def install(test_paths: list[str] | None = None) -> None:
    global _finder
    if _finder is not None:
        return
    if test_paths is None:
        test_paths = [os.getcwd()]
    _finder = TaintFinder(test_paths)
    sys.meta_path.insert(0, _finder)


def uninstall() -> None:
    global _finder
    if _finder is not None:
        sys.meta_path.remove(_finder)
        _finder = None
