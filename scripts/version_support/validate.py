from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from typing import Any

import riotfile


def _malformed_spec_error(message: str) -> RuntimeError:
    return RuntimeError(f"VERSION_SUPPORT_SPEC_JSON is malformed, {message}")


def _as_list(value: Any, field_name: str) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [value]
    raise _malformed_spec_error(f"{field_name} must be a string or a list")


def _expand_python_versions(value: object, field_name: str) -> list[str]:
    pys: list[str] = []
    for py in _as_list(value, field_name):
        if not isinstance(py, str):
            raise _malformed_spec_error(f"{field_name} values must be strings")

        for version in py.split(","):
            version = version.strip()
            if not version:
                raise _malformed_spec_error(f"{field_name} contains an empty version")
            if version.endswith("+"):
                min_version = version[:-1]
                if not min_version:
                    raise _malformed_spec_error(f"{field_name} version range must include a minimum version")
                pys.extend(riotfile.select_pys(min_version=min_version))
            else:
                pys.append(version)
    return pys


def validate_test_spec(test_spec: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    integrations = test_spec.get("integrations")
    if not isinstance(integrations, list):
        raise _malformed_spec_error("integrations must be a list")

    normalized: dict[str, list[dict[str, Any]]] = {}
    for integration in integrations:
        if not isinstance(integration, dict):
            raise _malformed_spec_error("each integration must be an object")

        name = integration.get("name")
        if not isinstance(name, str) or not name:
            raise _malformed_spec_error("each integration must have a name")

        targets = integration.get("targets")
        if not isinstance(targets, list) or not targets:
            raise _malformed_spec_error(f"integration {name} must have a non-empty targets list")

        normalized[name] = []
        for target in targets:
            if not isinstance(target, dict):
                raise _malformed_spec_error(f"integration {name} targets must be objects")

            if "py" not in target:
                raise _malformed_spec_error(f"integration {name} target is missing py")
            if "version_to_test" not in target:
                raise _malformed_spec_error(f"integration {name} target is missing version_to_test")

            package = target.get("package", name)
            if not isinstance(package, str) or not package:
                raise _malformed_spec_error(f"integration {name} target package must be a string")

            pkgs = target.get("pkgs", {})
            if not isinstance(pkgs, dict):
                raise _malformed_spec_error(f"integration {name} target pkgs must be an object")

            env = target.get("env")
            if env is not None and not isinstance(env, dict):
                raise _malformed_spec_error(f"integration {name} target env must be an object")

            pys = _expand_python_versions(target["py"], f"integration {name} target py")
            normalized[name].append(
                {
                    "pys": pys,
                    "version_to_test": _as_list(target["version_to_test"], "target version_to_test"),
                    "package": package,
                    "pkgs": pkgs,
                    "env": env,
                }
            )

    return normalized
