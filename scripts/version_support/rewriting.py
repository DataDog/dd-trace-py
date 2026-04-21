from __future__ import annotations

import ast
from collections.abc import Sequence

from .models import Expr
from .models import IntegrationSpec
from .models import PackageSpecValue
from .models import RiotVenv
from .models import SourceSpan
from .models import VersionSpec


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


def _expr(value: str | Expr) -> ast.expr:
    if isinstance(value, Expr):
        return ast.Name(id=value.code, ctx=ast.Load())
    return ast.Constant(value=value)


def _package_value(value: PackageSpecValue) -> ast.expr:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Expr)):
        return ast.List(elts=[_expr(item) for item in value], ctx=ast.Load())
    return _expr(value)  # type: ignore[arg-type]


def _render_pkgs(pkgs: dict[str, PackageSpecValue]) -> ast.Dict:
    keys: list[ast.expr] = []
    values: list[ast.expr] = []
    for package_name, package_value in pkgs.items():
        keys.append(ast.Constant(value=package_name))
        values.append(_package_value(package_value))
    return ast.Dict(keys=keys, values=values)


def _render_pys(version_spec: VersionSpec) -> ast.expr:
    selectors = version_spec.pys.versions
    if len(selectors) == 1 and not selectors[0].endswith("+"):
        return ast.Constant(value=selectors[0])
    if len(selectors) == 1 and selectors[0].endswith("+"):
        min_selector = selectors[0].removesuffix("+")
        return ast.Call(
            func=ast.Name(id="select_pys", ctx=ast.Load()),
            args=[],
            keywords=[ast.keyword(arg="min_version", value=ast.Constant(value=min_selector))],
        )
    if any(selector.endswith("+") for selector in selectors[1:]):
        return ast.List(elts=[ast.Constant(value=selector) for selector in selectors], ctx=ast.Load())
    if _selectors_are_contiguous(selectors):
        min_selector = selectors[0]
        max_selector = selectors[-1]
        keywords = [ast.keyword(arg="min_version", value=ast.Constant(value=min_selector))]
        if max_selector != min_selector:
            keywords.append(ast.keyword(arg="max_version", value=ast.Constant(value=max_selector)))
        return ast.Call(func=ast.Name(id="select_pys", ctx=ast.Load()), args=[], keywords=keywords)
    return ast.List(elts=[ast.Constant(value=selector) for selector in selectors], ctx=ast.Load())


def _selectors_are_contiguous(selectors: tuple[str, ...]) -> bool:
    parsed = [_parse_python_selector(selector) for selector in selectors]
    if any(item is None for item in parsed):
        return False
    major_minor = parsed  # type: ignore[assignment]
    return all(
        major_minor[index][0] == major_minor[index - 1][0] and major_minor[index][1] == major_minor[index - 1][1] + 1
        for index in range(1, len(major_minor))
    )


def _parse_python_selector(selector: str) -> tuple[int, int] | None:
    if selector.endswith("+"):
        return None
    parts = selector.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def _build_version_pkgs(integration_spec: IntegrationSpec, version_spec: VersionSpec) -> dict[str, PackageSpecValue]:
    pkgs: dict[str, PackageSpecValue] = {}
    if version_spec.pkgs:
        pkgs.update(version_spec.pkgs)
    target_package = version_spec.package or integration_spec.name
    pkgs[target_package] = version_spec.version_to_test
    return pkgs


def _render_version_venv(integration_spec: IntegrationSpec, version_spec: VersionSpec) -> ast.Call:
    keywords: list[ast.keyword] = [
        ast.keyword(arg="pys", value=_render_pys(version_spec)),
        ast.keyword(arg="pkgs", value=_render_pkgs(_build_version_pkgs(integration_spec, version_spec))),
    ]
    if version_spec.env:
        keywords.append(
            ast.keyword(
                arg="env",
                value=ast.Dict(
                    keys=[ast.Constant(value=key) for key in version_spec.env],
                    values=[ast.Constant(value=value) for value in version_spec.env.values()],
                ),
            )
        )
    if version_spec.command:
        keywords.append(ast.keyword(arg="command", value=ast.Constant(value=version_spec.command)))
    return ast.Call(func=ast.Name(id="Venv", ctx=ast.Load()), args=[], keywords=keywords)


def _replace_keyword_or_append(keywords: list[ast.keyword], *, arg: str, value: ast.expr) -> list[ast.keyword]:
    updated: list[ast.keyword] = []
    replaced = False
    for keyword in keywords:
        if keyword.arg == arg:
            updated.append(ast.keyword(arg=arg, value=value))
            replaced = True
            continue
        updated.append(keyword)
    if not replaced:
        updated.append(ast.keyword(arg=arg, value=value))
    return updated


def _replace_suite_from_spec(venv_call: ast.Call, integration_spec: IntegrationSpec) -> ast.Call:
    new_keywords = list(venv_call.keywords)
    if integration_spec.shared_pkgs is not None:
        new_keywords = _replace_keyword_or_append(
            new_keywords,
            arg="pkgs",
            value=_render_pkgs(integration_spec.shared_pkgs),
        )
    new_keywords = _replace_keyword_or_append(
        new_keywords,
        arg="venvs",
        value=ast.List(
            elts=[_render_version_venv(integration_spec, version_spec) for version_spec in integration_spec.versions],
            ctx=ast.Load(),
        ),
    )
    replaced_call = ast.Call(func=venv_call.func, args=venv_call.args, keywords=new_keywords)
    return ast.fix_missing_locations(ast.copy_location(replaced_call, venv_call))


def regenerate_suite(
    riotfile_source: str,
    *,
    suite: RiotVenv,
    integration_spec: IntegrationSpec,
) -> str:
    if suite.name != integration_spec.name:
        raise RuntimeError(f"Suite mismatch: got {suite.name!r}, expected integration {integration_spec.name!r}")
    if suite.call is None or suite.span is None:
        raise RuntimeError(f"Suite {integration_spec.name!r} is missing AST/source location metadata")

    replaced_call = _replace_suite_from_spec(suite.call, integration_spec)
    replacement = _render_venv_call(replaced_call, base_indent=suite.span.col_offset)
    start, end = _span_to_offsets(riotfile_source, suite.span)
    return f"{riotfile_source[:start]}{replacement}{riotfile_source[end:]}"
