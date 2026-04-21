from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path

from .models import Expr
from .models import FallbackSpec
from .models import IntegrationSpec
from .models import PackageSpecValue
from .models import RiotVenv
from .models import TargetSpec
from .models import integration
from .models import latest
from .models import py
from .models import version


@dataclass(frozen=True)
class _NormalizedTarget:
    py_selector: str
    version_to_test: PackageSpecValue
    pkgs: dict[str, PackageSpecValue] | None = None
    env: dict[str, str] | None = None
    command: str | None = None
    package: str | None = None


def _expr_from_text(text: str) -> str | Expr:
    if text == "latest":
        return latest
    return text


def _package_value(raw_value: object) -> PackageSpecValue:
    if isinstance(raw_value, str):
        return _expr_from_text(raw_value)
    if isinstance(raw_value, list) and raw_value and all(isinstance(item, str) for item in raw_value):
        return [_expr_from_text(item) for item in raw_value]
    raise ValueError(f"Unsupported package spec value: {raw_value!r}")


def _package_map(raw_pkgs: object, *, field_name: str) -> dict[str, PackageSpecValue]:
    if raw_pkgs is None:
        return {}
    if not isinstance(raw_pkgs, dict):
        raise ValueError(f"{field_name} must be an object mapping package names to version specs")
    parsed: dict[str, PackageSpecValue] = {}
    for package_name, package_value in raw_pkgs.items():
        if not isinstance(package_name, str):
            raise ValueError(f"{field_name} contains a non-string package name: {package_name!r}")
        parsed[package_name] = _package_value(package_value)
    return parsed


def _env_map(raw_env: object, *, field_name: str) -> dict[str, str]:
    if raw_env is None:
        return {}
    if not isinstance(raw_env, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in raw_env.items()
    ):
        raise ValueError(f"{field_name} must be an object of string values")
    return raw_env


def _parse_py_selectors(py_selector: str, *, integration_name: str) -> list[str]:
    selectors = [selector.strip() for selector in py_selector.split(",")]
    if not selectors or any(not selector for selector in selectors):
        raise ValueError(f"target entry for {integration_name!r} has an invalid 'py' selector list: {py_selector!r}")

    deduplicated: list[str] = []
    seen: set[str] = set()
    for selector in selectors:
        if selector.endswith("+"):
            _parse_python_version(selector.removesuffix("+"))
        else:
            _parse_python_version(selector)
        if selector not in seen:
            seen.add(selector)
            deduplicated.append(selector)
    return deduplicated


def _parse_target(raw_item: object, *, integration_name: str) -> list[TargetSpec]:
    if not isinstance(raw_item, dict):
        raise ValueError(f"target entry for {integration_name!r} must be an object: {raw_item!r}")
    py_selector = raw_item.get("py")
    if not isinstance(py_selector, str) or not py_selector:
        raise ValueError(f"target entry for {integration_name!r} requires a non-empty 'py' string")
    if "version_to_test" not in raw_item:
        raise ValueError(f"target entry for {integration_name!r} requires 'version_to_test'")
    package = raw_item.get("package")
    if package is not None and not isinstance(package, str):
        raise ValueError(f"target 'package' for {integration_name!r} must be a string when provided")
    version_to_test = _package_value(raw_item.get("version_to_test"))
    pkgs = _package_map(raw_item.get("pkgs"), field_name="pkgs") or None
    env = _env_map(raw_item.get("env"), field_name="env") or None
    selectors = _parse_py_selectors(py_selector, integration_name=integration_name)
    return [
        TargetSpec(
            py=selector,
            version_to_test=version_to_test,
            pkgs=pkgs,
            env=env,
            package=package,
        )
        for selector in selectors
    ]


def _parse_python_version(selector: str) -> tuple[int, int]:
    parts = selector.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"Unsupported Python version selector: {selector!r}")
    return int(parts[0]), int(parts[1])


def _selector_sort_key(selector: str) -> tuple[int, int, int]:
    if selector.endswith("+"):
        major, minor = _parse_python_version(selector.removesuffix("+"))
        return (major, minor, 1)
    major, minor = _parse_python_version(selector)
    return (major, minor, 0)


def _selector_to_canonical_range(
    raw_selector: str | list[str] | None,
) -> tuple[str, tuple[int, int] | None, tuple[int, int] | None]:
    if raw_selector is None:
        return ("unknown", None, None)
    if isinstance(raw_selector, list):
        versions = tuple(_parse_python_version(item) for item in raw_selector)
        if len(versions) != 1:
            return ("list", None, None)
        return ("exact", versions[0], versions[0])
    if raw_selector.startswith("select_pys("):
        parsed = ast.parse(raw_selector, mode="eval")
        expression = parsed.body
        if (
            not isinstance(expression, ast.Call)
            or not isinstance(expression.func, ast.Name)
            or expression.func.id != "select_pys"
        ):
            raise ValueError(f"Unsupported parsed python selector: {raw_selector!r}")
        min_version: tuple[int, int] | None = None
        max_version: tuple[int, int] | None = None
        for keyword in expression.keywords:
            if (
                keyword.arg == "min_version"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                min_version = _parse_python_version(keyword.value.value)
            elif (
                keyword.arg == "max_version"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                max_version = _parse_python_version(keyword.value.value)
        return ("range", min_version, max_version)
    version = _parse_python_version(raw_selector)
    return ("exact", version, version)


def _canonical_selector_text(raw_selector: str | list[str] | None) -> str | None:
    kind, min_version, max_version = _selector_to_canonical_range(raw_selector)
    if kind == "exact" and min_version is not None:
        return f"{min_version[0]}.{min_version[1]}"
    if kind == "range" and min_version is not None and max_version is None:
        return f"{min_version[0]}.{min_version[1]}+"
    return None


def _selector_matches(base_selector: str | list[str] | None, target_selector: str) -> bool:
    base_kind, base_min, base_max = _selector_to_canonical_range(base_selector)
    if target_selector.endswith("+"):
        if base_kind != "range" or base_min is None or base_max is not None:
            return False
        return _canonical_selector_text(base_selector) == target_selector

    target_version = _parse_python_version(target_selector)
    if base_kind == "exact":
        return base_min == target_version
    if base_kind == "range":
        if base_min is not None and target_version < base_min:
            return False
        if base_max is not None and target_version > base_max:
            return False
        return True
    if isinstance(base_selector, list):
        return target_selector in base_selector
    return False


def _find_matching_nested_venv(suite: RiotVenv, target_selector: str) -> RiotVenv | None:
    if not suite.venvs:
        return None
    matches = [nested for nested in suite.venvs if _selector_matches(nested.pys, target_selector)]
    if not matches:
        inferred = _infer_closest_nested_venv(suite, target_selector)
        if inferred is not None:
            return inferred
        raise ValueError(
            f"Unable to infer base packages for {suite.name!r} target {target_selector!r} from riotfile.py"
        )
    if len(matches) == 1:
        return matches[0]
    for nested in matches:
        if _canonical_selector_text(nested.pys) == target_selector:
            return nested
    raise ValueError(f"Ambiguous base subvenv match for {suite.name!r} target {target_selector!r}")


def _version_distance(left: tuple[int, int], right: tuple[int, int]) -> int:
    return abs((left[0] * 100 + left[1]) - (right[0] * 100 + right[1]))


def _distance_to_interval(
    target: tuple[int, int], min_version: tuple[int, int] | None, max_version: tuple[int, int] | None
) -> int:
    if min_version is not None and target < min_version:
        return _version_distance(target, min_version)
    if max_version is not None and target > max_version:
        return _version_distance(target, max_version)
    return 0


def _selector_versions(raw_selector: str | list[str] | None) -> tuple[tuple[int, int], ...]:
    if raw_selector is None:
        return ()
    if isinstance(raw_selector, list):
        return tuple(_parse_python_version(item) for item in raw_selector)
    if raw_selector.startswith("select_pys("):
        return ()
    return (_parse_python_version(raw_selector),)


def _inference_score(base_selector: str | list[str] | None, target_selector: str) -> tuple[int, int, int, int] | None:
    if target_selector.endswith("+"):
        target_min = _parse_python_version(target_selector.removesuffix("+"))
        kind, min_version, max_version = _selector_to_canonical_range(base_selector)
        if kind == "range" and min_version is not None:
            span = 10_000 if max_version is None else _version_distance(min_version, max_version)
            return (
                _distance_to_interval(target_min, min_version, max_version),
                _version_distance(target_min, min_version),
                0 if max_version is None else 1,
                -span,
            )
        versions = _selector_versions(base_selector)
        if versions:
            closest = min(_version_distance(target_min, version) for version in versions)
            return (closest, closest, 2, 0)
        return None

    target_version = _parse_python_version(target_selector)
    kind, min_version, max_version = _selector_to_canonical_range(base_selector)
    if kind == "range" and min_version is not None:
        span = 10_000 if max_version is None else _version_distance(min_version, max_version)
        return (
            _distance_to_interval(target_version, min_version, max_version),
            1,
            0 if max_version is not None else 1,
            span,
        )
    versions = _selector_versions(base_selector)
    if versions:
        closest = min(_version_distance(target_version, version) for version in versions)
        return (closest, 0, 0, 0)
    return None


def _infer_closest_nested_venv(suite: RiotVenv, target_selector: str) -> RiotVenv | None:
    if not suite.venvs:
        return None
    scored: list[tuple[tuple[int, int, int, int], RiotVenv]] = []
    for nested in suite.venvs:
        score = _inference_score(nested.pys, target_selector)
        if score is None:
            continue
        scored.append((score, nested))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    return scored[0][1]


def _target_package_name(
    suite_name: str,
    target: TargetSpec,
    suite_pkgs: dict[str, PackageSpecValue],
    base_pkgs: dict[str, PackageSpecValue],
) -> str:
    if target.package is not None:
        return target.package
    if suite_name in suite_pkgs or suite_name in base_pkgs:
        return suite_name
    if len(base_pkgs) == 1:
        return next(iter(base_pkgs))
    if len(suite_pkgs) == 1:
        return next(iter(suite_pkgs))
    candidates = sorted({*suite_pkgs, *base_pkgs})
    if candidates:
        raise ValueError(
            f"Unable to infer target package for {suite_name!r}; add 'package' to the target spec. "
            f"Candidate packages: {', '.join(candidates)}"
        )
    return suite_name


def _merge_effective_pkgs(
    suite_name: str, target: TargetSpec, base_pkgs: dict[str, PackageSpecValue], target_package: str
) -> dict[str, PackageSpecValue] | None:
    effective = dict(base_pkgs)
    effective.pop(suite_name, None)
    effective.pop(target_package, None)
    if target.pkgs:
        effective.update(target.pkgs)
    return effective or None


def _merge_effective_env(target: TargetSpec, base_env: dict[str, str]) -> dict[str, str] | None:
    effective = dict(base_env)
    if target.env:
        effective.update(target.env)
    return effective or None


def _normalize_target(integration_name: str, suite: RiotVenv, target: TargetSpec) -> _NormalizedTarget:
    base_venv = _find_matching_nested_venv(suite, target.py)
    suite_pkgs = dict(suite.pkgs or {})
    base_pkgs = dict(base_venv.pkgs or {}) if base_venv is not None else {}
    base_env = dict(base_venv.env or {}) if base_venv is not None else {}
    target_package = _target_package_name(integration_name, target, suite_pkgs, base_pkgs)
    return _NormalizedTarget(
        py_selector=target.py,
        version_to_test=target.version_to_test,
        pkgs=_merge_effective_pkgs(integration_name, target, base_pkgs, target_package),
        env=_merge_effective_env(target, base_env),
        command=base_venv.command if base_venv is not None else None,
        package=target_package,
    )


def _freeze_package_value(value: PackageSpecValue) -> tuple:
    if isinstance(value, Expr):
        return ("expr", value.code)
    if isinstance(value, str):
        return ("str", value)
    return ("seq", tuple(_freeze_package_value(item) for item in value))


def _freeze_package_map(pkgs: dict[str, PackageSpecValue] | None) -> tuple[tuple[str, tuple], ...]:
    if not pkgs:
        return ()
    return tuple(sorted((name, _freeze_package_value(package_value)) for name, package_value in pkgs.items()))


def _freeze_env_map(env: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not env:
        return ()
    return tuple(sorted(env.items()))


def _build_version_specs_for_selectors(target: _NormalizedTarget, selectors: list[str]) -> list:
    unique_selectors = sorted(set(selectors), key=_selector_sort_key)
    plus_selectors = [selector for selector in unique_selectors if selector.endswith("+")]
    if len(plus_selectors) > 1:
        raise ValueError(f"Cannot merge multiple '+' selectors for one target payload: {unique_selectors!r}")

    exact_selectors = [selector for selector in unique_selectors if not selector.endswith("+")]
    version_specs = []
    if plus_selectors:
        plus_selector = plus_selectors[0]
        plus_version = _parse_python_version(plus_selector.removesuffix("+"))
        exact_before_plus = [selector for selector in exact_selectors if _parse_python_version(selector) < plus_version]
        if exact_before_plus and _can_fold_exacts_into_plus(exact_before_plus, plus_version):
            version_specs.append(
                version(
                    target.version_to_test,
                    pys=py(f"{exact_before_plus[0]}+"),
                    pkgs=target.pkgs,
                    env=target.env,
                    command=target.command,
                    package=target.package,
                )
            )
        else:
            if exact_before_plus:
                version_specs.append(
                    version(
                        target.version_to_test,
                        pys=py(*exact_before_plus),
                        pkgs=target.pkgs,
                        env=target.env,
                        command=target.command,
                        package=target.package,
                    )
                )
            version_specs.append(
                version(
                    target.version_to_test,
                    pys=py(plus_selector),
                    pkgs=target.pkgs,
                    env=target.env,
                    command=target.command,
                    package=target.package,
                )
            )
        return version_specs

    return [
        version(
            target.version_to_test,
            pys=py(*exact_selectors),
            pkgs=target.pkgs,
            env=target.env,
            command=target.command,
            package=target.package,
        )
    ]


def _can_fold_exacts_into_plus(exact_selectors: list[str], plus_version: tuple[int, int]) -> bool:
    parsed = [_parse_python_version(selector) for selector in exact_selectors]
    if not parsed:
        return False
    if parsed[-1][0] != plus_version[0] or parsed[-1][1] + 1 != plus_version[1]:
        return False
    return all(
        parsed[index][0] == parsed[index - 1][0] and parsed[index][1] == parsed[index - 1][1] + 1
        for index in range(1, len(parsed))
    )


def _normalize_targets(integration_name: str, suite: RiotVenv, targets: list[TargetSpec]) -> IntegrationSpec:
    grouped: dict[tuple, tuple[_NormalizedTarget, list[str]]] = {}
    for target in targets:
        normalized = _normalize_target(integration_name, suite, target)
        key = (
            _freeze_package_value(normalized.version_to_test),
            _freeze_package_map(normalized.pkgs),
            _freeze_env_map(normalized.env),
            normalized.command,
            normalized.package,
        )
        if key not in grouped:
            grouped[key] = (normalized, [])
        grouped[key][1].append(normalized.py_selector)

    parsed_versions = []
    for normalized, selectors in grouped.values():
        parsed_versions.extend(_build_version_specs_for_selectors(normalized, selectors))
    return integration(integration_name, versions=parsed_versions)


def _parse_integration(raw_item: object, *, suites: dict[str, RiotVenv]) -> IntegrationSpec | FallbackSpec:
    if not isinstance(raw_item, dict):
        raise ValueError(f"integration entry must be an object: {raw_item!r}")
    name = raw_item.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"integration entry requires a non-empty 'name': {raw_item!r}")

    fallback_reason = raw_item.get("fallback_reason")
    if fallback_reason is not None:
        if not isinstance(fallback_reason, str) or not fallback_reason:
            raise ValueError(f"'fallback_reason' for {name!r} must be a non-empty string")
        return FallbackSpec(name=name, reason=fallback_reason)

    if "shared_pkgs" in raw_item:
        raise ValueError(f"integration {name!r} no longer supports 'shared_pkgs'; inherit from riotfile.py instead")
    if "versions" in raw_item:
        raise ValueError(f"integration {name!r} no longer supports 'versions'; use 'targets' instead")

    raw_targets = raw_item.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError(f"integration {name!r} requires a non-empty 'targets' array")

    suite = suites.get(name)
    if suite is None:
        raise ValueError(f"Unable to find suite {name!r} in riotfile.py for target normalization")

    parsed_targets = [
        target for raw_target in raw_targets for target in _parse_target(raw_target, integration_name=name)
    ]
    return _normalize_targets(name, suite, parsed_targets)


def load_specs_from_json_text(
    spec_json: str, *, suites: dict[str, RiotVenv]
) -> dict[str, IntegrationSpec | FallbackSpec]:
    raw_data = json.loads(spec_json)
    raw_integrations = raw_data.get("integrations") if isinstance(raw_data, dict) else None
    if not isinstance(raw_integrations, list):
        raise ValueError("spec JSON must contain an 'integrations' array")

    parsed: dict[str, IntegrationSpec | FallbackSpec] = {}
    for raw_item in raw_integrations:
        parsed_item = _parse_integration(raw_item, suites=suites)
        parsed[parsed_item.name] = parsed_item
    return parsed


def load_specs_from_json(spec_file: Path, *, suites: dict[str, RiotVenv]) -> dict[str, IntegrationSpec | FallbackSpec]:
    return load_specs_from_json_text(spec_file.read_text(encoding="utf-8"), suites=suites)
