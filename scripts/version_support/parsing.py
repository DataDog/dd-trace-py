import ast

from .models import RiotVenv
from .models import SourceSpan


def _expr_to_value(node: ast.AST) -> object:
    """Convert AST nodes while preserving container shapes.

    For non-literal leaves (e.g. ``latest``), returns expression text.
    """
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, (ast.List, ast.Tuple)):
        return [_expr_to_value(element) for element in node.elts]

    if isinstance(node, ast.Dict):
        converted: dict[object, object] = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                continue
            converted[_expr_to_value(key)] = _expr_to_value(value)
        return converted

    return ast.unparse(node)


def _as_str(value: object | None) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _as_str_dict(value: object | None) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        return value
    return None


def _as_pys(value: object | None) -> str | list[str] | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _as_dict(value: object | None) -> dict | None:
    if isinstance(value, dict):
        return value
    return None


def _call_span(node: ast.Call) -> SourceSpan:
    if node.end_lineno is None or node.end_col_offset is None:
        raise RuntimeError("Unable to determine source span for suite definition")

    return SourceSpan(
        lineno=node.lineno,
        col_offset=node.col_offset,
        end_lineno=node.end_lineno,
        end_col_offset=node.end_col_offset,
    )


def parse_sub_venv(venv_call: ast.Call) -> RiotVenv:
    kwargs: dict[str, object] = {}
    nested_venvs: list[RiotVenv] | None = None

    for keyword in venv_call.keywords:
        if keyword.arg is None:
            continue

        if keyword.arg == "venvs" and isinstance(keyword.value, ast.List):
            nested_venvs = []
            for item in keyword.value.elts:
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "Venv":
                    nested_venvs.append(parse_sub_venv(item))
            continue

        kwargs[keyword.arg] = _expr_to_value(keyword.value)

    return RiotVenv(
        name=_as_str(kwargs.get("name")),
        env=_as_str_dict(kwargs.get("env")),
        pys=_as_pys(kwargs.get("pys")),
        command=_as_str(kwargs.get("command")),
        pkgs=_as_dict(kwargs.get("pkgs")),
        venvs=nested_venvs,
        call=venv_call,
        span=_call_span(venv_call),
    )


def collect_test_suites(main_venv_call: ast.Call) -> dict[str, RiotVenv]:
    """Collect suites directly attached to the root riot venv."""

    sub_venvs: ast.List | None = None

    for keyword in main_venv_call.keywords:
        if keyword.arg != "venvs" or not isinstance(keyword.value, ast.List):
            continue

        sub_venvs = keyword.value
        break

    if not sub_venvs:
        return {}

    riot_venvs: dict[str, RiotVenv] = {}
    for item in sub_venvs.elts:
        if not (isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "Venv"):
            continue

        parsed_venv = parse_sub_venv(item)
        if parsed_venv.name is None:
            continue
        riot_venvs[parsed_venv.name] = parsed_venv
    return riot_venvs


def collect_venvs(riotfile_source: str, filename: str) -> dict[str, RiotVenv]:
    parsed = ast.parse(riotfile_source, filename=filename)

    for node in parsed.body:
        if not isinstance(node, ast.Assign):
            continue

        node_value = node.value
        if isinstance(node_value, ast.Call) and isinstance(node_value.func, ast.Name) and node_value.func.id == "Venv":
            return collect_test_suites(node_value)

    raise RuntimeError(f"Unable to find root `venv = Venv(...)` in {filename}")
