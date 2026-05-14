#!/usr/bin/env python3
"""Manage supported-configurations.json and the generated Python module.

Reads the JSON registry and produces a Python module with:
- SUPPORTED_CONFIGURATIONS: frozenset of all registered env var names
- CONFIGURATION_ALIASES: dict mapping env var name to list of aliases
- DEPRECATED_CONFIGURATIONS: frozenset of deprecated env var names

Also verifies that every DD_*/_DD_*/OTEL_*/DATADOG_* var accessed in ddtrace/ is registered.

Usage:
    python scripts/supported_configurations.py           # generate + verify registry
    python scripts/supported_configurations.py --check   # verify module is in sync + verify registry
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = REPO_ROOT / "supported-configurations.json"
OUTPUT_FILE = REPO_ROOT / "ddtrace" / "internal" / "settings" / "_supported_configurations.py"

HEADER = """\
# AUTO-GENERATED from supported-configurations.json — do not edit manually.
# Run: python scripts/supported_configurations.py
#
# This module provides fast O(1) lookups for environment variable validation
# in ddtrace/internal/settings/env.py.
"""


def generate_module(data: dict) -> str:
    configs = data["supportedConfigurations"]
    all_names = sorted(configs.keys())

    # Schema v2: each value is a single-element array
    entries = {name: configs[name][0] for name in all_names}
    aliases = {name: e["aliases"] for name, e in entries.items() if e.get("aliases")}
    deprecated = sorted(name for name, e in entries.items() if e.get("deprecated"))

    supported = "\n".join(f'        "{n}",' for n in all_names)

    def _format_alias_entry(name: str, vals: list[str], max_len: int = 120) -> str:
        single = '    "{}": [{}],'.format(name, ", ".join('"{}"'.format(a) for a in vals))
        if len(single) <= max_len:
            return single
        return '    "{}": [\n{}\n    ],'.format(name, "\n".join(f'        "{a}",' for a in vals))

    alias_lines = "\n".join(_format_alias_entry(n, v) for n, v in aliases.items())

    aliases_block = (
        f"CONFIGURATION_ALIASES: dict[str, list[str]] = {{\n{alias_lines}\n}}"
        if aliases
        else "CONFIGURATION_ALIASES: dict[str, list[str]] = {}"
    )
    deprecated_lines = "\n".join(f'        "{n}",' for n in deprecated)
    deprecated_block = (
        f"DEPRECATED_CONFIGURATIONS: frozenset[str] = frozenset(\n    {{\n{deprecated_lines}\n    }}\n)"
        if deprecated
        else "DEPRECATED_CONFIGURATIONS: frozenset[str] = frozenset()"
    )

    return f"""\
{HEADER}
SUPPORTED_CONFIGURATIONS: frozenset[str] = frozenset(
    {{
{supported}
    }}
)


{aliases_block}

{deprecated_block}
"""


def _is_envier_var_call(node: ast.AST) -> bool:
    """True for DDConfig.v(...) and DDConfig.var(...) — the envier calls that declare env vars."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("v", "var")
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "DDConfig"
    )


def _class_prefix(class_node: ast.ClassDef) -> str | None:
    """Return the literal value of ``__prefix__`` set on this class, if any.

    Handles both ``__prefix__ = "x"`` and the chained ``__item__ = __prefix__ = "x"`` form
    that envier uses for nested sub-configs.
    """
    for stmt in class_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not (isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)):
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name) and target.id == "__prefix__":
                return stmt.value.value
    return None


def _find_includes(module: ast.Module) -> dict[str, tuple[str, str]]:
    """Map ``SubClass -> (ParentClass, namespace)`` for ``Parent.include(Sub, namespace="x")`` calls.

    Envier sub-configs that aren't lexically nested inside their parent are attached this way.
    """
    found: dict[str, tuple[str, str]] = {}
    for stmt in module.body:
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
            continue
        call = stmt.value
        if not (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "include"
            and isinstance(call.func.value, ast.Name)
            and call.args
            and isinstance(call.args[0], ast.Name)
        ):
            continue
        for kw in call.keywords:
            if kw.arg == "namespace" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                found[call.args[0].id] = (call.func.value.id, kw.value.value)
                break
    return found


def _is_private_call(call: ast.Call) -> bool:
    """True if a DDConfig.v/var call has ``private=True``, which makes envier prefix ``_``."""
    return any(
        kw.arg == "private" and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in call.keywords
    )


def _walk_envier_class(class_node: ast.ClassDef, chain: list[str], out: set[str]) -> None:
    """Emit envier env vars from this class's body, recursing into lexically nested classes.

    ``chain`` is the fully-resolved prefix chain for this class — the caller is
    responsible for incorporating ``__prefix__`` or the ``.include`` namespace.
    """
    for stmt in class_node.body:
        if isinstance(stmt, ast.Assign) and _is_envier_var_call(stmt.value):
            args = stmt.value.args
            if len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
                # envier builds env names by joining chain + key on ".", uppercasing,
                # then replacing "." with "_". private=True adds a leading underscore.
                name = ".".join(chain + [args[1].value]).upper().replace(".", "_")
                out.add(f"_{name}" if _is_private_call(stmt.value) else name)
        elif isinstance(stmt, ast.ClassDef):
            inner = _class_prefix(stmt)
            _walk_envier_class(stmt, chain + [inner] if inner else chain, out)


def _chain_for(name: str, top_classes: dict[str, ast.ClassDef], includes: dict[str, tuple[str, str]]) -> list[str]:
    """Compute a class's full prefix chain, following ``.include`` parents transitively."""
    if name in includes:
        parent, namespace = includes[name]
        parent_chain = _chain_for(parent, top_classes, includes) if parent in top_classes else []
        return parent_chain + [namespace]
    prefix = _class_prefix(top_classes[name])
    return [prefix] if prefix else []


def _scan_envier_module(module: ast.Module, out: set[str]) -> None:
    """Emit envier-declared env vars from a parsed module's top-level classes."""
    includes = _find_includes(module)
    top_classes = {n.name: n for n in module.body if isinstance(n, ast.ClassDef)}
    for name, node in top_classes.items():
        _walk_envier_class(node, _chain_for(name, top_classes, includes), out)


def check_registry(data: dict) -> int:
    """Verify every DD_*/_DD_*/OTEL_*/DATADOG_* var referenced in ddtrace/ is in the registry."""
    configs = data["supportedConfigurations"]
    all_known: set[str] = set(configs.keys()) | {
        alias for entries in configs.values() for entry in entries for alias in entry.get("aliases", [])
    }

    missing: set[str] = set()
    envier_vars: set[str] = set()

    # Single pass over ddtrace/ Python files: (1) regex-scan the source text for any quoted
    # DD_*/_DD_*/OTEL_*/DATADOG_* literal, then (2) AST-scan for envier-declared vars whose
    # full name is built from DDConfig.v/var calls. The generated registry module is skipped
    # to avoid self-referential matches.
    pattern = re.compile(r'["\']((DD_|_DD_|OTEL_|DATADOG_)[A-Z][A-Z0-9_]*)["\']')
    for path in (REPO_ROOT / "ddtrace").rglob("*.py"):
        if path == OUTPUT_FILE:
            continue
        text = path.read_text(errors="ignore")
        for m in pattern.finditer(text):
            var = m.group(1)
            if var not in all_known:
                missing.add(var)
        # ast.parse is expensive; skip files that can't define envier configs.
        if "DDConfig" in text:
            try:
                _scan_envier_module(ast.parse(text), envier_vars)
            except SyntaxError:
                pass

    # Dynamic vars from PATCH_MODULES: DD_TRACE_{NAME}_ENABLED, DD_{NAME}_SERVICE[_NAME]
    assigns = {
        node.targets[0].id: node.value
        for node in ast.walk(ast.parse((REPO_ROOT / "ddtrace" / "_monkey.py").read_text()))
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in ("PATCH_MODULES", "_NOT_PATCHABLE_VIA_ENVVAR")
    }
    pm_node = assigns["PATCH_MODULES"]
    assert isinstance(pm_node, ast.Dict)  # nosec B101
    patch_modules = [k.value for k in pm_node.keys if isinstance(k, ast.Constant)]

    np_node = assigns["_NOT_PATCHABLE_VIA_ENVVAR"]
    assert isinstance(np_node, (ast.Set, ast.List, ast.Tuple))  # nosec B101
    not_patchable = {e.value for e in np_node.elts if isinstance(e, ast.Constant)}

    for name in patch_modules:
        n = name.upper()
        if name not in not_patchable and f"DD_TRACE_{n}_ENABLED" not in all_known:
            missing.add(f"DD_TRACE_{n}_ENABLED")
        for var in (f"DD_{n}_SERVICE", f"DD_{n}_SERVICE_NAME"):
            if var not in all_known:
                missing.add(var)

    for var in envier_vars:
        if var not in all_known:
            missing.add(var)

    if missing:
        print(
            f"ERROR: {len(missing)} var(s) found in ddtrace/ but missing from {INPUT_FILE.name}.\n"
            f"\n"
            f"To fix:\n"
            f"  1. Add the missing var(s) to supported-configurations.json\n"
            f"  2. Run: python scripts/supported_configurations.py\n"
            f"  3. Register the var in the central Configuration Registry (internal contributors): https://feature-parity.us1.prod.dog/#/configurations?viewType=configurations\n"
            f"  4. Stage the updated files and commit\n"
            f"\n"
            f"Unregistered vars:"
        )
        for var in sorted(missing):
            print(f"  {var}")
        return 1

    print(f"Registry is complete ({len(all_known)} entries, no unregistered vars).")
    return 0


def main() -> int:
    check_mode = "--check" in sys.argv

    if not INPUT_FILE.exists():
        print(f"ERROR: {INPUT_FILE} not found.")
        return 1

    with open(INPUT_FILE) as f:
        data = json.load(f)

    content = generate_module(data)

    if check_mode:
        if not OUTPUT_FILE.exists():
            print(f"ERROR: {OUTPUT_FILE} does not exist. Run without --check to generate it.")
            return 1

        existing = OUTPUT_FILE.read_text()
        if existing != content:
            print(f"ERROR: {OUTPUT_FILE} is out of date. Run without --check to regenerate it.")
            return 1

        print("_supported_configurations.py is up to date.")
        return check_registry(data)

    # Generate mode: write the module then verify registry completeness.
    OUTPUT_FILE.write_text(content)
    config_count = len(data["supportedConfigurations"])
    print(f"Generated {OUTPUT_FILE} with {config_count} entries.")
    return check_registry(data)


if __name__ == "__main__":
    sys.exit(main())
