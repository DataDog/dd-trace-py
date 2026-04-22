import ast
from dataclasses import dataclass
from typing import Sequence


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


@dataclass(frozen=True)
class Expr:
    code: str


latest = Expr("latest")


PackageValue = str | Expr
PackageSpecValue = PackageValue | Sequence[PackageValue]


@dataclass(frozen=True)
class PythonSpec:
    versions: tuple[str, ...]


def py(*versions: str) -> PythonSpec:
    if not versions:
        raise ValueError("py(...) requires at least one Python version selector")
    return PythonSpec(versions=tuple(versions))


@dataclass(frozen=True)
class TargetSpec:
    py: str
    version_to_test: PackageSpecValue
    pkgs: dict[str, PackageSpecValue] | None = None
    env: dict[str, str] | None = None
    package: str | None = None


@dataclass(frozen=True)
class VersionSpec:
    version_to_test: PackageSpecValue
    pys: PythonSpec
    pkgs: dict[str, PackageSpecValue] | None = None
    env: dict[str, str] | None = None
    package: str | None = None


def version(
    version_to_test: PackageSpecValue,
    *,
    pys: PythonSpec,
    pkgs: dict[str, PackageSpecValue] | None = None,
    env: dict[str, str] | None = None,
    package: str | None = None,
) -> VersionSpec:
    return VersionSpec(
        version_to_test=version_to_test,
        pys=pys,
        pkgs=pkgs,
        env=env,
        package=package,
    )


@dataclass(frozen=True)
class IntegrationSpec:
    name: str
    versions: tuple[VersionSpec, ...]
    shared_pkgs: dict[str, PackageSpecValue] | None = None


def integration(
    name: str,
    *,
    shared_pkgs: dict[str, PackageSpecValue] | None = None,
    versions: Sequence[VersionSpec],
) -> IntegrationSpec:
    if not name:
        raise ValueError("integration(...) requires a non-empty name")
    if not versions:
        raise ValueError("integration(...) requires at least one version(...) row")
    return IntegrationSpec(name=name, shared_pkgs=shared_pkgs, versions=tuple(versions))


@dataclass(frozen=True)
class FallbackSpec:
    name: str
    reason: str


def fallback(name: str, *, reason: str) -> FallbackSpec:
    if not name:
        raise ValueError("fallback(...) requires a non-empty integration name")
    return FallbackSpec(name=name, reason=reason)
