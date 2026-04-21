from __future__ import annotations

import ast

from .models import RiotVenv
from .models import SourceSpan


def _position_to_offset(source: str, *, lineno: int, col_offset: int) -> int:
    lines = source.splitlines(keepends=True)
    return sum(len(line) for line in lines[: lineno - 1]) + col_offset


def _span_to_offsets(source: str, span: SourceSpan) -> tuple[int, int]:
    start = _position_to_offset(source, lineno=span.lineno, col_offset=span.col_offset)
    end = _position_to_offset(source, lineno=span.end_lineno, col_offset=span.end_col_offset)
    return start, end


def _render_venv_call(venv_call: ast.Call, base_indent: int) -> str:
    unparsed = ast.unparse(venv_call)
    indent = " " * base_indent
    return "\n".join(f"{indent}{line}" for line in unparsed.splitlines())


def _replace_suite_versions(venv_call: ast.Call, suite_name: str, versions: list[str]) -> ast.Call:
    new_keywords: list[ast.keyword] = []
    found_pkgs = False
    found_suite_pkg = False

    for keyword in venv_call.keywords:
        if keyword.arg != "pkgs":
            new_keywords.append(keyword)
            continue

        if not isinstance(keyword.value, ast.Dict):
            raise RuntimeError(f"Suite {suite_name!r} has a non-dict pkgs definition")

        found_pkgs = True
        updated_values: list[ast.expr] = []
        for key_node, value_node in zip(keyword.value.keys, keyword.value.values):
            if isinstance(key_node, ast.Constant) and key_node.value == suite_name:
                value_node = ast.List(
                    elts=[ast.Constant(value=version) for version in versions],
                    ctx=ast.Load(),
                )
                found_suite_pkg = True
            updated_values.append(value_node)

        new_keywords.append(
            ast.keyword(arg=keyword.arg, value=ast.Dict(keys=keyword.value.keys, values=updated_values))
        )

    if not found_pkgs:
        raise RuntimeError(f"Suite {suite_name!r} does not define pkgs")
    if not found_suite_pkg:
        raise RuntimeError(f"Suite {suite_name!r} pkgs does not contain package {suite_name!r}")

    replaced_call = ast.Call(func=venv_call.func, args=venv_call.args, keywords=new_keywords)
    return ast.fix_missing_locations(ast.copy_location(replaced_call, venv_call))


def regenerate_suite(
    riotfile_source: str,
    *,
    suite: RiotVenv,
    suite_name: str,
    versions: list[str],
) -> str:
    if suite.venvs:
        raise RuntimeError(f"Suite {suite_name!r} contains nested venvs and is not supported yet")
    if suite.call is None or suite.span is None:
        raise RuntimeError(f"Suite {suite_name!r} is missing AST/source location metadata")

    replaced_call = _replace_suite_versions(suite.call, suite_name, versions)
    replacement = _render_venv_call(replaced_call, base_indent=suite.span.col_offset)
    start, end = _span_to_offsets(riotfile_source, suite.span)
    return f"{riotfile_source[:start]}{replacement}{riotfile_source[end:]}"
