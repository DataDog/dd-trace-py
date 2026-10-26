#!/usr/bin/env python3
"""Fail if a branch adds Python that is missing type annotations.

New functions need a type on every parameter (except self and cls) and a
return type. A parameter added to a function that already existed needs a
type too. A new simple name — a local, a module or class global, or
``self.field`` inside ``__init__`` — needs ``name: T = ...``.

Names this branch did not introduce are left alone. Loop targets, ``with``
and ``except`` names, tuple unpacks, and walrus bindings are not checked:
those lines cannot carry an inline annotation.

Only .py files are checked. Vendor trees and generated *_pb2.py files are
skipped. Cython, C, and the rest of the existing tree are out of scope.

Usage:

    python scripts/check_new_untyped_defs.py --base-ref REF
    python scripts/check_new_untyped_defs.py --staged
"""

from __future__ import annotations

import argparse
import ast
import subprocess  # nosec: B404
import sys
from typing import TypeAlias


_Func: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef


def _is_checked_python(path: str) -> bool:
    if not path.endswith(".py"):
        return False
    if path.endswith("_pb2.py"):
        return False
    return "/vendor/" not in f"/{path}"


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _skip_receiver(node: _Func) -> bool:
    for decorator in node.decorator_list:
        if _decorator_name(decorator) == "staticmethod":
            return False
    return True


def _params(node: _Func) -> list[tuple[str, ast.arg]]:
    items: list[tuple[str, ast.arg]] = []
    positional: list[ast.arg] = [*node.args.posonlyargs, *node.args.args]
    if positional and _skip_receiver(node) and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    for arg in positional:
        items.append((arg.arg, arg))
    for arg in node.args.kwonlyargs:
        items.append((arg.arg, arg))
    if node.args.vararg is not None:
        items.append((f"*{node.args.vararg.arg}", node.args.vararg))
    if node.args.kwarg is not None:
        items.append((f"**{node.args.kwarg.arg}", node.args.kwarg))
    return items


def _functions(tree: ast.AST) -> list[tuple[str, _Func]]:
    found: list[tuple[str, _Func]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname: str = f"{prefix}{child.name}"
                found.append((qualname, child))
                walk(child, f"{qualname}.")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _missing_detail(labels: list[str], *, missing_return: bool) -> str | None:
    parts: list[str] = []
    if labels:
        parts.append("parameter " + ", ".join(labels))
    if missing_return:
        parts.append("return")
    if not parts:
        return None
    return "missing type on " + " and ".join(parts)


def _parse(source: str, path: str) -> ast.AST | None:
    try:
        return ast.parse(source, filename=path)
    except SyntaxError:
        return None


def _child_bodies(stmt: ast.stmt) -> list[list[ast.stmt]]:
    bodies: list[list[ast.stmt]] = []
    if isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
        bodies.append(stmt.body)
        orelse: list[ast.stmt] = getattr(stmt, "orelse", [])
        if orelse:
            bodies.append(orelse)
    elif isinstance(stmt, ast.Try):
        bodies.append(stmt.body)
        bodies.extend(handler.body for handler in stmt.handlers)
        bodies.append(stmt.orelse)
        bodies.append(stmt.finalbody)
    bodies.extend(_optional_stmt_bodies(stmt, "TryStar"))
    bodies.extend(_optional_stmt_bodies(stmt, "Match"))
    return bodies


def _as_body(value: object) -> list[ast.stmt] | None:
    if isinstance(value, list) and all(isinstance(item, ast.stmt) for item in value):
        return value
    return None


def _optional_stmt_bodies(stmt: ast.stmt, node_name: str) -> list[list[ast.stmt]]:
    node_type: type[ast.stmt] | None = getattr(ast, node_name, None)
    if node_type is None or not isinstance(stmt, node_type):
        return []
    bodies: list[list[ast.stmt]] = []
    body: list[ast.stmt] | None = _as_body(getattr(stmt, "body", None))
    if body is not None:
        bodies.append(body)
    for nested_name in ("orelse", "finalbody"):
        nested: list[ast.stmt] | None = _as_body(getattr(stmt, nested_name, None))
        if nested:
            bodies.append(nested)
    for group_name in ("handlers", "cases"):
        group: object = getattr(stmt, group_name, None)
        if not isinstance(group, list):
            continue
        for item in group:
            item_body: list[ast.stmt] | None = _as_body(getattr(item, "body", None))
            if item_body is not None:
                bodies.append(item_body)
    return bodies


def _record_target(
    target: ast.expr,
    lineno: int,
    annotated: bool,
    found: list[tuple[str, int, bool, str]],
    *,
    in_init: bool,
) -> None:
    if isinstance(target, ast.Name):
        if target.id == "_":
            return
        found.append((target.id, lineno, annotated, "name"))
        return
    if (
        in_init
        and isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    ):
        found.append((target.attr, lineno, annotated, "attr"))


def _bindings(body: list[ast.stmt], *, in_init: bool) -> list[tuple[str, int, bool, str]]:
    """Return (name, line, annotated, kind) for simple bindings in this body.

    Nested functions and classes are separate scopes. Loop targets, ``with``
    and ``except`` names, tuple unpacks, and walrus bindings are omitted.
    """
    found: list[tuple[str, int, bool, str]] = []

    def visit(statements: list[ast.stmt]) -> None:
        for stmt in statements:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(stmt, ast.AnnAssign):
                _record_target(stmt.target, stmt.lineno, True, found, in_init=in_init)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    _record_target(target, stmt.lineno, False, found, in_init=in_init)
            for nested in _child_bodies(stmt):
                visit(nested)

    visit(body)
    return found


def _receiver_names(node: _Func) -> set[str]:
    positional: list[ast.arg] = [*node.args.posonlyargs, *node.args.args]
    if positional and _skip_receiver(node) and positional[0].arg in {"self", "cls"}:
        return {positional[0].arg}
    return set()


def _class_bodies(tree: ast.AST) -> list[tuple[str, list[ast.stmt]]]:
    found: list[tuple[str, list[ast.stmt]]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qualname: str = f"{prefix}{child.name}"
                found.append((qualname, child.body))
                walk(child, f"{qualname}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def _new_binding_violations(
    head_body: list[ast.stmt],
    base_bodies: list[list[ast.stmt]],
    qualname: str,
    *,
    in_init: bool,
    skip_names: set[str],
) -> list[tuple[int, str, str]]:
    known: set[tuple[str, str]] = set()
    for base_body in base_bodies:
        for name, _line, _annotated, kind in _bindings(base_body, in_init=in_init):
            known.add((kind, name))

    annotated: set[tuple[str, str]] = set()
    pending: dict[tuple[str, str], tuple[int, str]] = {}
    for name, lineno, is_annotated, kind in _bindings(head_body, in_init=in_init):
        key: tuple[str, str] = (kind, name)
        if key in known or (kind == "name" and name in skip_names):
            continue
        if is_annotated:
            annotated.add(key)
            pending.pop(key, None)
            continue
        if key in annotated or key in pending:
            continue
        if kind == "attr":
            detail: str = f"missing type on self.{name}"
        else:
            detail = f"missing type on {name}"
        pending[key] = (lineno, detail)
    return [(lineno, qualname, detail) for lineno, detail in pending.values()]


def find_violations(path: str, base_source: str | None, head_source: str) -> list[tuple[int, str, str]]:
    """Return (line, qualname, detail) for new untyped functions, parameters, and names."""
    if not _is_checked_python(path):
        return []

    head_tree: ast.AST | None = _parse(head_source, path)
    if head_tree is None:
        return []

    base_tree: ast.AST | None = _parse(base_source, path) if base_source is not None else None
    base_by_name: dict[str, list[_Func]] = {}
    if base_tree is not None:
        for qualname, func in _functions(base_tree):
            base_by_name.setdefault(qualname, []).append(func)

    violations: list[tuple[int, str, str]] = []
    for qualname, func in _functions(head_tree):
        base_funcs: list[_Func] = base_by_name.get(qualname, [])
        if not base_funcs:
            missing_labels: list[str] = [label for label, arg in _params(func) if arg.annotation is None]
            detail: str | None = _missing_detail(missing_labels, missing_return=func.returns is None)
            if detail is not None:
                violations.append((func.lineno, qualname, detail))
        else:
            known_params: set[str] = {label for base_func in base_funcs for label, _arg in _params(base_func)}
            new_untyped: list[tuple[int, str]] = [
                (arg.lineno, label)
                for label, arg in _params(func)
                if label not in known_params and arg.annotation is None
            ]
            if new_untyped:
                labels: list[str] = [label for _line, label in new_untyped]
                added_detail: str | None = _missing_detail(labels, missing_return=False)
                if added_detail is not None:
                    violations.append((new_untyped[0][0], qualname, added_detail))

        skip_names: set[str] = {arg.arg for _label, arg in _params(func)} | _receiver_names(func)
        violations.extend(
            _new_binding_violations(
                func.body,
                [base_func.body for base_func in base_funcs],
                qualname,
                in_init=func.name == "__init__",
                skip_names=skip_names,
            )
        )

    head_module: list[ast.stmt] = head_tree.body if isinstance(head_tree, ast.Module) else []
    base_module: list[list[ast.stmt]] = [base_tree.body] if isinstance(base_tree, ast.Module) else []
    violations.extend(_new_binding_violations(head_module, base_module, "<module>", in_init=False, skip_names=set()))

    base_classes: dict[str, list[ast.stmt]] = dict(_class_bodies(base_tree)) if base_tree is not None else {}
    for qualname, body in _class_bodies(head_tree):
        base_class_body: list[ast.stmt] | None = base_classes.get(qualname)
        base_class_bodies: list[list[ast.stmt]] = [base_class_body] if base_class_body is not None else []
        violations.extend(_new_binding_violations(body, base_class_bodies, qualname, in_init=False, skip_names=set()))

    return sorted(violations)


def _run_git(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec: B603, B607
        ["git", *args],
        capture_output=True,
        check=check,
        text=True,
    )


def _parse_name_status(output: str) -> list[tuple[str, str | None]]:
    """Return (head path, base path). Base path is None when the file is new."""
    rows: list[tuple[str, str | None]] = []
    for line in output.splitlines():
        parts: list[str] = line.split("\t")
        if len(parts) < 2:
            continue
        status: str = parts[0]
        if status.startswith(("R", "C")) and len(parts) >= 3:
            rows.append((parts[2], parts[1]))
        elif status == "A":
            rows.append((parts[1], None))
        else:
            rows.append((parts[1], parts[1]))
    return rows


def _show(spec: str) -> str | None:
    result: subprocess.CompletedProcess[str] = _run_git(["show", spec], check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _collect(*, staged: bool, base_ref: str) -> list[tuple[str, int, str, str]]:
    if staged:
        status: subprocess.CompletedProcess[str] = _run_git(
            ["diff", "--name-status", "--find-renames", "--diff-filter=ACMR", "--staged"]
        )
        files: list[tuple[str, str | None]] = _parse_name_status(status.stdout)

        def base_of(base_path: str) -> str | None:
            return _show(f"HEAD:{base_path}")

        def head_of(head_path: str) -> str | None:
            return _show(f":{head_path}")

    else:
        merge_base: str = _run_git(["merge-base", base_ref, "HEAD"]).stdout.strip()
        status = _run_git(["diff", "--name-status", "--find-renames", "--diff-filter=ACMR", merge_base, "HEAD"])
        files = _parse_name_status(status.stdout)

        def base_of(base_path: str) -> str | None:
            return _show(f"{merge_base}:{base_path}")

        def head_of(head_path: str) -> str | None:
            return _show(f"HEAD:{head_path}")

    found: list[tuple[str, int, str, str]] = []
    for head_path, base_path in files:
        if not _is_checked_python(head_path):
            continue
        head_source: str | None = head_of(head_path)
        if head_source is None:
            continue
        base_source: str | None = base_of(base_path) if base_path is not None else None
        found.extend(
            (head_path, line, qualname, detail)
            for line, qualname, detail in find_violations(head_path, base_source, head_source)
        )
    return found


def main(argv: list[str] | None = None) -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base-ref", default="origin/main", help="Git ref to diff against.")
    parser.add_argument("--staged", action="store_true", help="Check the index against HEAD.")
    args: argparse.Namespace = parser.parse_args(argv)

    if args.staged:
        print("Comparing staged Python against HEAD")
    else:
        print(f"Comparing against base ref: {args.base_ref}")

    try:
        violations: list[tuple[str, int, str, str]] = _collect(staged=args.staged, base_ref=args.base_ref)
    except subprocess.CalledProcessError as exc:
        print(f"error: failed to compute diff: {exc.stderr}", file=sys.stderr)
        return 1

    if not violations:
        print("OK: new Python has type annotations.")
        return 0

    print("ERROR: new Python is missing type annotations.", file=sys.stderr)
    print(
        "Add inline types on functions, parameters, locals, and globals this branch introduces.\n"
        "Write name: T = ... for a new local, global, or self.field in __init__.\n"
        "This check does not scan the rest of the repository.",
        file=sys.stderr,
    )
    for path, line, qualname, detail in violations:
        print(f"  {path}:{line}: {qualname} {detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
