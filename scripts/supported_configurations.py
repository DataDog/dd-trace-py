#!/usr/bin/env python3
"""Manage supported-configurations.json and the generated Python module.

Reads the JSON registry and produces a Python module with:
- SUPPORTED_CONFIGURATIONS: frozenset of all registered env var names
- CONFIGURATION_ALIASES: dict mapping env var name to list of aliases
- DEPRECATED_CONFIGURATIONS: frozenset of deprecated env var names
- CONFIGURATION_TYPES: dict mapping env var name to registry type string
- CONFIGURATION_DEFAULTS: dict mapping env var name to raw default string (or None)

Also verifies that every DD_*/_DD_*/OTEL_*/DATADOG_* var accessed in ddtrace/ is registered.

Usage:
    python scripts/supported_configurations.py           # generate + verify registry
    python scripts/supported_configurations.py --check   # verify module is in sync + verify registry
"""

from __future__ import annotations

import ast
from collections.abc import Callable
import json
from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_FILE = REPO_ROOT / "supported-configurations.json"
OUTPUT_FILE = REPO_ROOT / "ddtrace" / "internal" / "settings" / "_supported_configurations.py"
CONFIG_REGISTRY_URL = "https://feature-parity.us1.prod.dog/#/configurations?viewType=configurations"

HEADER = """\
# AUTO-GENERATED from supported-configurations.json — do not edit manually.
# Run: python scripts/supported_configurations.py
#
# This module provides fast O(1) lookups for environment variable validation
# in ddtrace/internal/settings/env.py, and registry type/default data used by
# ddtrace/internal/settings/_core.py for registry-backed field() declarations.
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

    # Build CONFIGURATION_TYPES and CONFIGURATION_DEFAULTS alphabetically.
    types_lines = "\n".join(f'    "{n}": "{entries[n]["type"]}",' for n in all_names)
    types_block = f"CONFIGURATION_TYPES: dict[str, str] = {{\n{types_lines}\n}}"

    def _default_literal(entry: dict) -> str:
        raw = entry.get("default")
        if raw is None:
            return "None"
        return json.dumps(raw, ensure_ascii=False)

    def _defaults_line(n: str) -> str:
        val = _default_literal(entries[n])
        line = f'    "{n}": {val},'
        return line if len(line) <= 120 else f"{line}  # noqa: E501"

    defaults_lines = "\n".join(_defaults_line(n) for n in all_names)
    defaults_block = f'CONFIGURATION_DEFAULTS: dict[str, "str | None"] = {{\n{defaults_lines}\n}}'

    return f"""\
{HEADER}
SUPPORTED_CONFIGURATIONS: frozenset[str] = frozenset(
    {{
{supported}
    }}
)


{aliases_block}

{deprecated_block}


{types_block}


{defaults_block}
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


_STRING_CONSTANTS_CACHE: dict[Path, dict[str, str]] = {}


def _literal_str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _collect_string_constants(module: ast.Module) -> dict[str, str]:
    """Collect simple string constants from a module without importing it.

    Supports top-level constants (``FOO = "bar"``) and class attributes
    (``Class.FOO = "bar"``), including annotated assignments.
    """
    constants: dict[str, str] = {}

    def add(name: str, value: ast.AST | None) -> None:
        if literal := _literal_str(value):
            constants[name] = literal

    for stmt in module.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    add(target.id, stmt.value)
        elif isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name):
                add(stmt.target.id, stmt.value)
        elif isinstance(stmt, ast.ClassDef):
            for child in stmt.body:
                if isinstance(child, ast.Assign):
                    for target in child.targets:
                        if isinstance(target, ast.Name):
                            add(f"{stmt.name}.{target.id}", child.value)
                elif isinstance(child, ast.AnnAssign):
                    if isinstance(child.target, ast.Name):
                        add(f"{stmt.name}.{child.target.id}", child.value)

    return constants


def _module_path(module_name: str) -> Path | None:
    module = REPO_ROOT.joinpath(*module_name.split("."))
    module_file = module.with_suffix(".py")
    if module_file.exists():
        return module_file
    package_file = module / "__init__.py"
    return package_file if package_file.exists() else None


def _string_constants_for_module(module_name: str) -> dict[str, str]:
    path = _module_path(module_name)
    if path is None:
        return {}
    if path not in _STRING_CONSTANTS_CACHE:
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            _STRING_CONSTANTS_CACHE[path] = {}
        else:
            try:
                _STRING_CONSTANTS_CACHE[path] = _collect_string_constants(ast.parse(text))
            except SyntaxError:
                _STRING_CONSTANTS_CACHE[path] = {}
    return _STRING_CONSTANTS_CACHE[path]


def _find_imported_names(module: ast.Module) -> dict[str, tuple[str, str]]:
    """Map local import names to ``(module, imported_name)`` for absolute ``from`` imports."""
    imported: dict[str, tuple[str, str]] = {}
    for stmt in module.body:
        if not isinstance(stmt, ast.ImportFrom) or stmt.module is None or stmt.level:
            continue
        for alias in stmt.names:
            # Look up by the local name, but keep the original imported name for resolving
            # constants from the source module (e.g. ``from x import Foo as Bar``).
            imported[alias.asname or alias.name] = (stmt.module, alias.name)
    return imported


def _dotted_name(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return None


def _resolve_env_name_arg(
    node: ast.AST,
    local_constants: dict[str, str],
    imported_names: dict[str, tuple[str, str]],
) -> str | None:
    if literal := _literal_str(node):
        return literal

    if isinstance(node, ast.Name):
        if node.id in local_constants:
            return local_constants[node.id]
        if imported := imported_names.get(node.id):
            module_name, imported_name = imported
            return _string_constants_for_module(module_name).get(imported_name)
        return None

    parts = _dotted_name(node)
    if not parts:
        return None

    local_key = ".".join(parts)
    if local_key in local_constants:
        return local_constants[local_key]

    if imported := imported_names.get(parts[0]):
        module_name, imported_name = imported
        imported_key = ".".join([imported_name] + parts[1:])
        return _string_constants_for_module(module_name).get(imported_key)

    return None


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


def _walk_envier_class(
    class_node: ast.ClassDef,
    chain: list[str],
    out: set[str],
    resolve_env_name_arg: Callable[[ast.AST], str | None],
    private_registry_mismatches: dict[str, str],
) -> None:
    """Emit envier env vars from this class's body, recursing into lexically nested classes.

    ``chain`` is the fully-resolved prefix chain for this class.
    ``private_registry_mismatches`` is populated with likely public registry
    spellings for ``private=True`` variables, mapped to the actual
    leading-underscore env var name.
    """
    for stmt in class_node.body:
        if isinstance(stmt, ast.Assign) and _is_envier_var_call(stmt.value):
            args = stmt.value.args
            env_name = resolve_env_name_arg(args[1]) if len(args) >= 2 else None
            if env_name is not None:
                # envier builds env names by joining chain + key on ".", uppercasing,
                # then replacing "." with "_". private=True adds a leading underscore.
                name = ".".join(chain + [env_name]).upper().replace(".", "_")
                if not _is_private_call(stmt.value):
                    out.add(name)
                else:
                    # envier private=True changes the env var spelling, not just telemetry
                    # visibility. Stale public registry entries would make env validation
                    # accept variables that settings never read.
                    private_name = f"_{name}"
                    out.add(private_name)
                    # Exact public spelling for this private variable.
                    private_registry_mismatches[name] = private_name
                    if len(chain) > 1:
                        # Common stale spelling for included sub-configs: parent prefix
                        # + variable name, but missing the sub-config's own prefix.
                        unscoped_name = ".".join(chain[:-1] + [env_name]).upper().replace(".", "_")
                        private_registry_mismatches[unscoped_name] = private_name
        elif isinstance(stmt, ast.ClassDef):
            inner = _class_prefix(stmt)
            _walk_envier_class(
                stmt,
                chain + [inner] if inner else chain,
                out,
                resolve_env_name_arg,
                private_registry_mismatches,
            )


def _chain_for(name: str, top_classes: dict[str, ast.ClassDef], includes: dict[str, tuple[str, str]]) -> list[str]:
    """Compute a class's full prefix chain, following ``.include`` parents transitively."""
    if name in includes:
        parent, _namespace = includes[name]
        parent_chain = _chain_for(parent, top_classes, includes) if parent in top_classes else []
        # Envier's include(namespace=...) sets the Python attribute namespace; the
        # environment variable prefix still comes from the included class's
        # __prefix__. In current settings these are usually equal, but keeping this
        # distinction avoids generating registry names from attribute paths.
        prefix = _class_prefix(top_classes[name])
        return parent_chain + ([prefix] if prefix else [])
    prefix = _class_prefix(top_classes[name])
    return [prefix] if prefix else []


def _scan_envier_module(
    module: ast.Module,
    out: set[str],
    private_registry_mismatches: dict[str, str],
) -> None:
    """Emit envier-declared env vars from a parsed module's top-level classes."""
    includes = _find_includes(module)
    top_classes = {n.name: n for n in module.body if isinstance(n, ast.ClassDef)}
    local_constants = _collect_string_constants(module)
    imported_names = _find_imported_names(module)

    def resolve_env_name_arg(node: ast.AST) -> str | None:
        return _resolve_env_name_arg(node, local_constants, imported_names)

    for name, node in top_classes.items():
        _walk_envier_class(
            node,
            _chain_for(name, top_classes, includes),
            out,
            resolve_env_name_arg,
            private_registry_mismatches,
        )


def check_registry(data: dict) -> int:
    """Verify every DD_*/_DD_*/OTEL_*/DATADOG_* var referenced in ddtrace/ is in the registry."""
    configs = data["supportedConfigurations"]
    all_known: set[str] = set(configs.keys()) | {
        alias for entries in configs.values() for entry in entries for alias in entry.get("aliases", [])
    }

    missing: set[str] = set()
    envier_vars: set[str] = set()
    private_registry_mismatches: dict[str, str] = {}

    # Single pass over ddtrace/ Python files: (1) regex-scan the source text for any quoted
    # DD_*/_DD_*/OTEL_*/DATADOG_* literal, then (2) AST-scan for envier-declared vars whose
    # full name is built from DDConfig.v/var calls. The generated registry module is skipped
    # to avoid self-referential matches.
    # The trailing [A-Z0-9] requires a quoted DD_*/OTEL_*/DATADOG_* literal to end in an
    # alphanumeric, so prefix-style strings ending in "_" (e.g. "DD_TRACE_") used for
    # startswith checks are not mistaken for unregistered configuration variables.
    pattern = re.compile(r'["\']((DD_|_DD_|OTEL_|DATADOG_)[A-Z][A-Z0-9_]*[A-Z0-9])["\']')
    for path in (REPO_ROOT / "ddtrace").rglob("*.py"):
        if path == OUTPUT_FILE:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for m in pattern.finditer(text):
            var = m.group(1)
            if var not in all_known:
                missing.add(var)
        # ast.parse is expensive; skip files that can't define envier configs.
        if "DDConfig" in text:
            try:
                module = ast.parse(text)
            except SyntaxError:
                pass
            else:
                _scan_envier_module(module, envier_vars, private_registry_mismatches)

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

    # The literal public name may appear in constants used as the DDConfig.v/var
    # argument even though envier reads the leading-underscore name for private=True.
    missing -= private_registry_mismatches.keys()

    private_envier_vars = set(private_registry_mismatches.values())
    bad_private_registrations = {
        public_name: private_name
        for public_name, private_name in private_registry_mismatches.items()
        if public_name in configs
    }
    non_internal_private_registrations = {
        private_name
        for private_name in private_envier_vars
        if private_name in configs and configs[private_name][0].get("internal") is not True
    }

    has_errors = False
    if missing:
        has_errors = True
        print(
            f"ERROR: {len(missing)} var(s) found in ddtrace/ but missing from {INPUT_FILE.name}.\n"
            f"\n"
            f"To fix:\n"
            f"  1. Add the missing var(s) to supported-configurations.json\n"
            f"  2. Run: python scripts/supported_configurations.py\n"
            f"  3. Register the var in the central Configuration Registry (internal contributors): "
            f"{CONFIG_REGISTRY_URL}\n"
            f"  4. Stage the updated files and commit\n"
            f"\n"
            f"Unregistered vars:"
        )
        for var in sorted(missing):
            print(f"  {var}")

    if bad_private_registrations:
        has_errors = True
        print(
            f"ERROR: {len(bad_private_registrations)} private envier var(s) are registered without their "
            f"private env var spelling.\n"
            f"\n"
            f"Entries declared with private=True must be registered using the exact envier name, including "
            f"the leading '_' and any sub-config prefix.\n"
            f"\n"
            f"Misregistered vars:"
        )
        for public_name, private_name in sorted(bad_private_registrations.items()):
            print(f"  {public_name} should be {private_name}")

    if non_internal_private_registrations:
        has_errors = True
        print(
            f"ERROR: {len(non_internal_private_registrations)} private envier var(s) are registered without "
            f"internal: true.\n"
            f"\n"
            f"Entries declared with private=True must be marked internal in {INPUT_FILE.name}.\n"
            f"\n"
            f"Non-internal private vars:"
        )
        for private_name in sorted(non_internal_private_registrations):
            print(f"  {private_name}")

    if has_errors:
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
