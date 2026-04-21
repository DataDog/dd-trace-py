import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpan:
    lineno: int
    col_offset: int
    end_lineno: int
    end_col_offset: int


@dataclass(frozen=True)
class RiotVenv:
    name: str | None = None
    env: dict[str, str] | None = None
    pys: str | list[str] | None = None
    command: str | None = None
    pkgs: dict | None = None
    venvs: list["RiotVenv"] | None = None
    call: ast.Call | None = None
    span: SourceSpan | None = None
