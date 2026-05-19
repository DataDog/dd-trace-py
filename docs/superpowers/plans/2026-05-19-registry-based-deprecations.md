# Registry-Based Env Var Deprecations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralize env var deprecation handling in `supported-configurations.json` and `env.py` so adding a deprecation requires only a registry edit (no manual `deprecate()` call at the "right line" in the codebase).

**Architecture:** Extend the JSON schema with optional deprecation metadata fields (`removal_version`, `replaced_by`, `extra_message`) on each entry and on each alias. The code generator emits a richer `DEPRECATED_CONFIGURATIONS: dict[str, DeprecationInfo]` (replacing the existing `frozenset`). `env.py` fires `DDTraceDeprecationWarning` once per process when a deprecated key is *actually read* (not just probed). Existing manual `deprecate()` call sites for env vars are removed.

**Tech Stack:** Python (3.9+), `ddtrace.vendor.debtcollector`, `supported-configurations.json` schema v2, `scripts/supported_configurations.py` generator, pytest.

---

## File Structure

**Modify:**
- `supported-configurations.json` — extend deprecation metadata on existing deprecated entries + entries previously deprecated only in code; mark deprecated aliases.
- `scripts/supported_configurations.py` — generator emits richer `DEPRECATED_CONFIGURATIONS` mapping; reads `removal_version`/`replaced_by`/`extra_message` from JSON, plus per-alias deprecation.
- `ddtrace/internal/settings/_supported_configurations.py` — regenerated output (auto-generated; do not hand-edit).
- `ddtrace/internal/settings/env.py` — fire `DDTraceDeprecationWarning` on first read of a deprecated key whose value is actually set in `os.environ`; keep existing debug-log behavior for probes.
- `ddtrace/internal/settings/_config.py` — remove manual `deprecate(...)` calls for `DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED` and `DD_TRACE_INFERRED_SPANS_ENABLED`.
- `ddtrace/contrib/internal/asgi/middleware.py` — remove the `log.warning` for `DD_ASGI_TRACE_WEBSOCKET`.
- `ddtrace/contrib/internal/pytest/_plugin_v2.py` — remove the manual `deprecate(...)` for `DD_PYTEST_USE_NEW_PLUGIN_BETA`.
- `tests/internal/test_env.py` — extend tests for new behavior.

**Create:**
- `tests/internal/test_supported_configurations_deprecations.py` — schema-level tests (generator output, JSON validation).
- `releasenotes/notes/registry-based-deprecations-<hash>.yaml` — single release note.

---

## Schema Design

All deprecation metadata lives inside an optional `deprecation: {...}` block on each entry. The existing `deprecated: true` boolean stays as the cross-tracer-compatible "headline" signal; the nested block carries the richer metadata.

**Entry-level deprecation** (variable will be removed; no replacement, or replacement is implicit):

```json
"DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": [
  {
    "implementation": "A",
    "type": "boolean",
    "default": "true",
    "deprecated": true,
    "deprecation": {
      "removal_version": "5.0.0",
      "extra_message": "128-bit trace ID generation will become mandatory in version 5.0.0."
    }
  }
]
```

**Alias-level deprecation** (variable was renamed; the old name is the alias and is deprecated, the canonical itself is not):

```json
"DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED": [
  {
    "implementation": "A",
    "type": "boolean",
    "default": "false",
    "aliases": ["DD_TRACE_INFERRED_SPANS_ENABLED"],
    "deprecation": {
      "aliases": {
        "DD_TRACE_INFERRED_SPANS_ENABLED": {
          "removal_version": "5.0.0",
          "replaced_by": "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED"
        }
      }
    }
  }
]
```

The three valid configurations:

| State | `deprecated` | `deprecation.removal_version` etc. | `deprecation.aliases` |
|---|---|---|---|
| Canonical deprecated only | `true` | optional | absent |
| Aliases deprecated only (canonical unaffected) | absent / false | absent | required |
| Both canonical and aliases deprecated | `true` | optional | optional |

The alias-only case is the common "rename" pattern (the new name is the canonical, the old name lives on as a deprecated alias). The canonical-only case is the "will be removed entirely" pattern. Both can coexist when an entry was renamed *and* the new name is itself slated for removal.

**Rules:**
- `deprecation` block is optional. If present, all fields inside it are optional too.
- `deprecation.removal_version`: optional string (semver). Forwarded to `debtcollector.deprecate(removal_version=...)`. Applies to the entry's own name.
- `deprecation.extra_message`: optional string. Forwarded to `debtcollector.deprecate(message=...)`. Applies to the entry's own name.
- `deprecation.replaced_by`: optional string. When present, the message becomes "Use `<replaced_by>` instead." (plus any `extra_message`).
- `deprecation.aliases`: optional dict keyed by alias name. Each value is an object with the same three optional fields (`removal_version`, `extra_message`, `replaced_by`), but they apply to *that alias name*, not the canonical entry. An entry can have `deprecation.aliases` populated **without** `deprecated: true` — that means the canonical itself is fine, but some of its aliases are deprecated.
- `deprecated: true` remains the boolean signal for *the canonical entry's own deprecation status*. It does not need to be set just because the entry has deprecated aliases. The generator enforces this: entry-level metadata fields (`removal_version`/`extra_message`/`replaced_by`) without `deprecated: true` is a schema error; `deprecation.aliases` without `deprecated: true` is fine.

The existing entries that already use `deprecated: true` (e.g. `DATADOG_TAGS`) need no schema change — the `deprecation` block is optional.

---

## Generator Output Design

`DEPRECATED_CONFIGURATIONS` changes from `frozenset[str]` to a typed dict:

```python
class DeprecationInfo(TypedDict, total=False):
    removal_version: str
    extra_message: str
    replaced_by: str

DEPRECATED_CONFIGURATIONS: dict[str, DeprecationInfo] = {
    "DATADOG_TAGS": {},
    "DATADOG_TRACE_AGENT_HOSTNAME": {},
    "DD_COMPILE_DEBUG": {},
    "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": {
        "removal_version": "5.0.0",
        "extra_message": "128-bit trace ID generation will become mandatory in version 5.0.0.",
    },
    "DD_TRACE_INFERRED_SPANS_ENABLED": {
        "removal_version": "5.0.0",
        "replaced_by": "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED",
    },
    ...
}
```

Deprecated *aliases* (from `deprecation.aliases` in the JSON) are flattened into this same dict — at runtime, a read of the alias name is what triggers the warning. The canonical name only appears in the map if its own entry has `deprecated: true`.

`TypedDict` import requires `from typing import TypedDict`. To keep the generated module light, generate plain `dict` literals with a comment-only type hint, no runtime `TypedDict` class. The header comment lists the recognized keys.

---

### Task 1: Add `DeprecationInfo` schema fields to JSON (manual edits)

Update entries in `supported-configurations.json` for the four env vars currently using manual `deprecate()`/`log.warning` calls.

**Files:**
- Modify: `supported-configurations.json`

- [ ] **Step 1: Mark `DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED` as deprecated**

Locate the entry (around line 3632) and replace with:

```json
    "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": [
      {
        "implementation": "A",
        "type": "boolean",
        "default": "true",
        "deprecated": true,
        "deprecation": {
          "removal_version": "5.0.0",
          "extra_message": "128-bit trace ID generation will become mandatory in version 5.0.0."
        }
      }
    ],
```

- [ ] **Step 2: Mark `DD_TRACE_INFERRED_SPANS_ENABLED` alias as deprecated**

Locate the `DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED` entry (around line 4240) and replace with:

```json
    "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED": [
      {
        "implementation": "A",
        "type": "boolean",
        "default": "false",
        "aliases": [
          "DD_TRACE_INFERRED_SPANS_ENABLED"
        ],
        "deprecation": {
          "aliases": {
            "DD_TRACE_INFERRED_SPANS_ENABLED": {
              "removal_version": "5.0.0",
              "replaced_by": "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED"
            }
          }
        }
      }
    ],
```

- [ ] **Step 3: Mark `DD_ASGI_TRACE_WEBSOCKET` as deprecated**

Locate `DD_ASGI_TRACE_WEBSOCKET` entry (around line 447) and replace with:

```json
    "DD_ASGI_TRACE_WEBSOCKET": [
      {
        "implementation": "A",
        "type": "boolean",
        "default": "true",
        "deprecated": true,
        "deprecation": {
          "replaced_by": "DD_TRACE_WEBSOCKET_MESSAGES_ENABLED"
        }
      }
    ],
```

(Verify the actual existing structure before editing — type/default must match the current code's value.)

- [ ] **Step 4: Mark `DD_PYTEST_USE_NEW_PLUGIN_BETA` as deprecated**

Locate `DD_PYTEST_USE_NEW_PLUGIN_BETA` entry (around line 3116) and replace with:

```json
    "DD_PYTEST_USE_NEW_PLUGIN_BETA": [
      {
        "implementation": "A",
        "type": "boolean",
        "default": "false",
        "deprecated": true,
        "deprecation": {
          "removal_version": "3.0.0",
          "extra_message": "The new pytest plugin is now the default. No configuration is required."
        }
      }
    ],
```

- [ ] **Step 5: Validate JSON parses cleanly**

Run: `python -c "import json; json.load(open('supported-configurations.json'))"`
Expected: no output (success).

- [ ] **Step 6: Commit**

```bash
git add supported-configurations.json
git commit -m "chore(config): add deprecation metadata for known deprecated env vars"
```

---

### Task 2: Write failing test for richer `DEPRECATED_CONFIGURATIONS` output

**Files:**
- Create: `tests/internal/test_supported_configurations_deprecations.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the deprecation metadata exposed by _supported_configurations."""
from ddtrace.internal.settings import _supported_configurations as sc


def test_deprecated_configurations_is_a_dict():
    assert isinstance(sc.DEPRECATED_CONFIGURATIONS, dict)


def test_known_deprecated_env_var_has_removal_version():
    info = sc.DEPRECATED_CONFIGURATIONS.get("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED")
    assert info is not None
    assert info["removal_version"] == "5.0.0"
    assert "128-bit trace ID" in info["extra_message"]


def test_deprecated_alias_in_deprecated_configurations():
    info = sc.DEPRECATED_CONFIGURATIONS.get("DD_TRACE_INFERRED_SPANS_ENABLED")
    assert info is not None
    assert info["replaced_by"] == "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED"


def test_canonical_of_deprecated_alias_is_not_itself_deprecated():
    # DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED has a deprecated alias, but the canonical
    # itself is not deprecated — it must NOT appear in DEPRECATED_CONFIGURATIONS.
    assert "DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED" not in sc.DEPRECATED_CONFIGURATIONS


def test_deprecated_marker_only_entries_have_empty_dict():
    # Vars marked deprecated but with no metadata (e.g. DATADOG_TAGS) appear with {}.
    assert sc.DEPRECATED_CONFIGURATIONS.get("DATADOG_TAGS") == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `scripts/run-tests tests/internal/test_supported_configurations_deprecations.py -v`
Expected: FAIL — `DEPRECATED_CONFIGURATIONS` is currently a `frozenset`, not a dict; `info["removal_version"]` raises `TypeError`.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/internal/test_supported_configurations_deprecations.py
git commit -m "test(config): add tests for registry-driven deprecation metadata"
```

---

### Task 3: Update generator to emit richer `DEPRECATED_CONFIGURATIONS`

**Files:**
- Modify: `scripts/supported_configurations.py`
- Modify (regenerated): `ddtrace/internal/settings/_supported_configurations.py`

- [ ] **Step 1: Replace `generate_module` deprecation logic**

In `scripts/supported_configurations.py`, replace the existing deprecated-block generation with a richer mapping. Edit the lines that compute `deprecated` and build `deprecated_block`:

```python
def generate_module(data: dict) -> str:
    configs = data["supportedConfigurations"]
    all_names = sorted(configs.keys())

    # Schema v2: each value is a single-element array
    entries = {name: configs[name][0] for name in all_names}
    aliases = {name: e["aliases"] for name, e in entries.items() if e.get("aliases")}

    # Build deprecation mapping: canonical name (if entry is deprecated) plus each deprecated alias.
    # Each value is a dict with optional keys: removal_version, extra_message, replaced_by.
    # Entry-level metadata comes from entry["deprecation"]; per-alias metadata from
    # entry["deprecation"]["aliases"]. The boolean entry["deprecated"] is the headline signal.
    deprecation_map: dict[str, dict[str, str]] = {}
    _META_KEYS = ("removal_version", "extra_message", "replaced_by")
    for name, entry in entries.items():
        block = entry.get("deprecation") or {}
        if entry.get("deprecated"):
            deprecation_map[name] = {k: block[k] for k in _META_KEYS if k in block}
        elif any(k in block for k in _META_KEYS):
            raise ValueError(
                f"{name}: entry-level deprecation metadata present but 'deprecated: true' missing"
            )
        for alias, alias_info in (block.get("aliases") or {}).items():
            deprecation_map[alias] = {k: alias_info[k] for k in _META_KEYS if k in alias_info}

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

    def _format_deprecation(name: str, info: dict[str, str]) -> str:
        if not info:
            return f'    "{name}": {{}},'
        kv = ", ".join(f'"{k}": "{v}"' for k, v in sorted(info.items()))
        return f'    "{name}": {{{kv}}},'

    if deprecation_map:
        deprecation_lines = "\n".join(_format_deprecation(n, deprecation_map[n]) for n in sorted(deprecation_map))
        deprecated_block = (
            f"# DEPRECATED_CONFIGURATIONS values may contain: removal_version, extra_message, replaced_by\n"
            f"DEPRECATED_CONFIGURATIONS: dict[str, dict[str, str]] = {{\n{deprecation_lines}\n}}"
        )
    else:
        deprecated_block = "DEPRECATED_CONFIGURATIONS: dict[str, dict[str, str]] = {}"

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
```

- [ ] **Step 2: Regenerate the module**

Run: `python scripts/supported_configurations.py`
Expected output ends with: `Registry is complete (... entries, no unregistered vars).`

- [ ] **Step 3: Run the failing tests; verify they now pass**

Run: `scripts/run-tests tests/internal/test_supported_configurations_deprecations.py -v`
Expected: PASS for all four tests.

- [ ] **Step 4: Run the existing `--check` mode to confirm CI compatibility**

Run: `python scripts/supported_configurations.py --check`
Expected: exit 0, prints "_supported_configurations.py is up to date." and registry-complete line.

- [ ] **Step 5: Commit**

```bash
git add scripts/supported_configurations.py ddtrace/internal/settings/_supported_configurations.py
git commit -m "feat(config): emit deprecation metadata in generated registry module"
```

---

### Task 4: Write failing test for centralized `DDTraceDeprecationWarning` in `env.py`

**Files:**
- Modify: `tests/internal/test_env.py`

- [ ] **Step 1: Append the failing test**

Append to `tests/internal/test_env.py`:

```python
import warnings

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning


def test_reading_deprecated_set_var_emits_ddtrace_deprecation_warning(monkeypatch):
    monkeypatch.setenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "false")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        env_module.dd_environ.get("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED")
    messages = [str(w.message) for w in caught if issubclass(w.category, DDTraceDeprecationWarning)]
    assert any("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in m for m in messages)
    assert any("5.0.0" in m for m in messages)


def test_reading_deprecated_alias_emits_ddtrace_deprecation_warning(monkeypatch):
    monkeypatch.setenv("DD_TRACE_INFERRED_SPANS_ENABLED", "true")
    monkeypatch.delenv("DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        env_module.dd_environ.get("DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED")
    messages = [str(w.message) for w in caught if issubclass(w.category, DDTraceDeprecationWarning)]
    assert any("DD_TRACE_INFERRED_SPANS_ENABLED" in m for m in messages)


def test_deprecation_warning_fires_only_once_per_key(monkeypatch):
    monkeypatch.setenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "false")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        env_module.dd_environ.get("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED")
        env_module.dd_environ.get("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED")
        env_module.dd_environ["DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED"]
    messages = [
        str(w.message) for w in caught
        if issubclass(w.category, DDTraceDeprecationWarning) and "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in str(w.message)
    ]
    assert len(messages) == 1


def test_contains_check_does_not_fire_deprecation_warning(monkeypatch):
    # __contains__ probes existence — should not fire a user-facing deprecation warning.
    monkeypatch.delenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        _ = "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in env_module.dd_environ
    relevant = [w for w in caught if "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in str(w.message)]
    assert relevant == []


def test_deprecated_unset_var_does_not_fire_warning(monkeypatch):
    # A deprecated var that the user did not set should not fire a warning on read.
    monkeypatch.delenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        env_module.dd_environ.get("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "true")
    relevant = [w for w in caught if "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in str(w.message)]
    assert relevant == []
```

Also extend the `reset_warned_keys` fixture to clear the new state set (added in Task 5):

```python
@pytest.fixture(autouse=True)
def reset_warned_keys():
    env_module._warned_keys.clear()
    env_module._deprecation_warned.clear()
    yield
    env_module._warned_keys.clear()
    env_module._deprecation_warned.clear()
```

- [ ] **Step 2: Run the failing tests**

Run: `scripts/run-tests tests/internal/test_env.py -v -k "deprecat or contains_check"`
Expected: FAIL — `env_module._deprecation_warned` does not exist; no `DDTraceDeprecationWarning` is emitted.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/internal/test_env.py
git commit -m "test(env): cover registry-driven deprecation warning emission"
```

---

### Task 5: Implement central `DDTraceDeprecationWarning` emission in `env.py`

**Files:**
- Modify: `ddtrace/internal/settings/env.py`

- [ ] **Step 1: Update `env.py` to fire `DDTraceDeprecationWarning` on read of a set deprecated key**

Replace the contents of `ddtrace/internal/settings/env.py` with the block below.

The `from __future__ import annotations` line must appear immediately after the module docstring — it enables PEP-604 union syntax (`str | None`) on Python 3.9, which we use in `_maybe_warn_deprecated_read`.

```python
"""Centralized environment variable access for dd-trace-py.

This module provides a drop-in replacement for os.environ, enabling centralized
control and validation of all environment variable access in ddtrace.

All DD_*/_DD_*/OTEL_*/DATADOG_* environment variable accesses are validated
against the registry in supported-configurations.json. Unregistered variables
produce a debug log.

Reads also honor ``CONFIGURATION_ALIASES`` from the registry: a read for a
canonical name falls back to its registered legacy aliases if the canonical
is unset. Aliases registered here must be pure renames (same value space) —
translations like OTEL→DD belong in ``_otel_remapper.py`` instead.

Deprecation handling is driven by ``DEPRECATED_CONFIGURATIONS`` in the
generated registry module. When a deprecated env var is **actually set** by
the user and read via this module, a ``DDTraceDeprecationWarning`` is emitted
once per process. Mere existence probes (``__contains__``) do not fire the
warning. To add a new deprecation, edit ``supported-configurations.json`` —
no code change is required.
"""
from __future__ import annotations

from collections.abc import MutableMapping
import logging
import os
from typing import Iterator

from ddtrace.internal.settings._supported_configurations import CONFIGURATION_ALIASES
from ddtrace.internal.settings._supported_configurations import DEPRECATED_CONFIGURATIONS
from ddtrace.internal.settings._supported_configurations import SUPPORTED_CONFIGURATIONS
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


# AIDEV-NOTE: Use stdlib logging here instead of ddtrace.internal.logger.get_logger.
# ddtrace/internal/logger.py imports ddtrace.internal.settings.env, so using
# get_logger() would create a circular import at module-load time.
logger = logging.getLogger(__name__)

_ALIAS_TARGETS: frozenset[str] = frozenset(alias for aliases in CONFIGURATION_ALIASES.values() for alias in aliases)
_warned_keys: set[str] = set()
_deprecation_warned: set[str] = set()


def _validate_key(key: str) -> None:
    """Warn if a DD_*/_DD_*/OTEL_*/DATADOG_* key is not in the supported-configurations registry.

    Keys that are registered aliases of a supported configuration are also accepted.
    Each unsupported key is warned about at most once per process.
    """
    if not (key.startswith("DD_") or key.startswith("_DD_") or key.startswith("OTEL_") or key.startswith("DATADOG_")):
        return

    if key in _warned_keys:
        return

    if key not in SUPPORTED_CONFIGURATIONS and key not in _ALIAS_TARGETS:
        _warned_keys.add(key)
        logger.debug("Unsupported Datadog configuration variable accessed: %s", key)
    elif key in DEPRECATED_CONFIGURATIONS:
        _warned_keys.add(key)
        logger.debug("Deprecated Datadog configuration variable accessed: %s", key)


def _emit_deprecation_warning(key: str) -> None:
    """Fire a once-per-process DDTraceDeprecationWarning for a deprecated env var the user actually set."""
    if key in _deprecation_warned:
        return
    info = DEPRECATED_CONFIGURATIONS.get(key)
    if info is None:
        return
    _deprecation_warned.add(key)

    parts = []
    if "replaced_by" in info:
        parts.append(f"Use {info['replaced_by']} instead.")
    if "extra_message" in info:
        parts.append(info["extra_message"])
    message = " ".join(parts) if parts else None

    deprecate(
        f"{key} is deprecated",
        message=message,
        removal_version=info.get("removal_version"),
        category=DDTraceDeprecationWarning,
    )


def _maybe_warn_deprecated_read(key: str, source_key: str | None) -> None:
    """Emit a deprecation warning if ``source_key`` (the actual env var found) is deprecated.

    ``key`` is what the caller asked for; ``source_key`` is the env name we resolved the value
    from (could be ``key`` itself, or one of its registered aliases). Either may be deprecated.
    """
    if source_key is not None and source_key in DEPRECATED_CONFIGURATIONS:
        _emit_deprecation_warning(source_key)
    if key != source_key and key in DEPRECATED_CONFIGURATIONS and key in os.environ:
        _emit_deprecation_warning(key)


class EnvConfig(MutableMapping):
    """A MutableMapping wrapper around os.environ.

    Serves as the centralized entry point for all environment variable access
    in dd-trace-py. Drop-in replacement for os.environ — supports reads, writes,
    deletes, containment checks, iteration, and all standard dict-like operations.

    Validates that DD_*/_DD_*/OTEL_*/DATADOG_* accesses use registered
    configuration variables from supported-configurations.json. Emits a
    DDTraceDeprecationWarning on reads of deprecated env vars whose values
    are set in os.environ.
    """

    def __getitem__(self, key: str) -> str:
        _validate_key(key)
        if (value := os.environ.get(key)) is not None:
            _maybe_warn_deprecated_read(key, key)
            return value
        for alias in CONFIGURATION_ALIASES.get(key, ()):
            if (value := os.environ.get(alias)) is not None:
                _maybe_warn_deprecated_read(key, alias)
                return value
        raise KeyError(key)

    def __setitem__(self, key: str, value: str) -> None:
        _validate_key(key)
        os.environ[key] = value

    def __delitem__(self, key: str) -> None:
        del os.environ[key]

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            _validate_key(key)
            if key in os.environ:
                return True
            return any(alias in os.environ for alias in CONFIGURATION_ALIASES.get(key, ()))
        return key in os.environ

    def __iter__(self) -> Iterator[str]:
        return iter(os.environ)

    def __len__(self) -> int:
        return len(os.environ)

    def copy(self) -> dict:
        return dict(self)


dd_environ = EnvConfig()
```

- [ ] **Step 2: Run the env tests; verify they pass**

Run: `scripts/run-tests tests/internal/test_env.py -v`
Expected: PASS — all existing tests plus the four new deprecation tests.

- [ ] **Step 3: Commit**

```bash
git add ddtrace/internal/settings/env.py
git commit -m "feat(env): fire DDTraceDeprecationWarning from registry on first read"
```

---

### Task 6: Write failing test confirming removal of duplicate warnings from `_config.py`

**Files:**
- Modify: `tests/internal/test_env.py`

- [ ] **Step 1: Append a test that triggers `_config.py`'s deprecation path**

Append to `tests/internal/test_env.py`:

```python
def test_loading_config_with_deprecated_var_fires_warning_exactly_once(monkeypatch):
    """Config() construction reads DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED.

    With the manual deprecate() removed from _config.py, the warning must come
    from env.py — and must fire exactly once even though _config.py reads the
    var through ``env`` containment and again through ``_get_config``.
    """
    monkeypatch.setenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "false")
    env_module._deprecation_warned.clear()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        from ddtrace.internal.settings._config import Config  # local import to avoid early-side-effect
        Config()

    relevant = [
        str(w.message) for w in caught
        if issubclass(w.category, DDTraceDeprecationWarning)
        and "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in str(w.message)
    ]
    assert len(relevant) == 1, relevant
```

- [ ] **Step 2: Run the test; verify it fails with TWO warnings**

Run: `scripts/run-tests tests/internal/test_env.py::test_loading_config_with_deprecated_var_fires_warning_exactly_once -v`
Expected: FAIL with `assert 2 == 1` (one from `env.py`, one from the still-present manual `deprecate()` in `_config.py`).

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/internal/test_env.py
git commit -m "test(env): assert single deprecation warning from config load"
```

---

### Task 7: Remove manual `deprecate()` calls for env vars from code

**Files:**
- Modify: `ddtrace/internal/settings/_config.py`
- Modify: `ddtrace/contrib/internal/asgi/middleware.py`
- Modify: `ddtrace/contrib/internal/pytest/_plugin_v2.py`

- [ ] **Step 1: Remove the `DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED` block in `_config.py`**

In `ddtrace/internal/settings/_config.py` around line 572, delete:

```python
        # Emit deprecation warning if env var is set (still functional, removal in 5.0.0)
        if "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in env:
            deprecate(
                "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED is deprecated",
                message="128-bit trace ID generation will become mandatory in version 5.0.0.",
                removal_version="5.0.0",
                category=DDTraceDeprecationWarning,
            )
```

Leave `_native_config.set_128_bit_trace_id_enabled(_get_config(...))` intact.

- [ ] **Step 2: Remove the `DD_TRACE_INFERRED_SPANS_ENABLED` block in `_config.py`**

Around line 698, delete:

```python
        if "DD_TRACE_INFERRED_SPANS_ENABLED" in env:
            deprecate(
                "DD_TRACE_INFERRED_SPANS_ENABLED is deprecated",
                message="Please use DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED instead.",
                removal_version="5.0.0",
                category=DDTraceDeprecationWarning,
            )
```

Leave the `_get_config([...], False, asbool)` call directly below it intact.

- [ ] **Step 3: Remove the `deprecate` import from `_config.py` if no longer used**

Run: `grep -c "deprecate(" ddtrace/internal/settings/_config.py`
If the count is `0`, remove these lines:
- `from ddtrace.vendor.debtcollector import deprecate`
- `from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning` (only if no other `DDTraceDeprecationWarning` reference remains; verify with `grep -c DDTraceDeprecationWarning ddtrace/internal/settings/_config.py`)

- [ ] **Step 4: Remove the `DD_ASGI_TRACE_WEBSOCKET` warning in `asgi/middleware.py`**

In `ddtrace/contrib/internal/asgi/middleware.py` around line 38, delete:

```python
if env.get("DD_ASGI_TRACE_WEBSOCKET") is not None:
    log.warning(
        "DD_ASGI_TRACE_WEBSOCKET is deprecated and will be removed in a future version. "
        "Use DD_TRACE_WEBSOCKET_MESSAGES_ENABLED instead."
    )
```

Note: this is a `log.warning`, not a `deprecate()`, but it's the user-facing deprecation signal for that var. The registry-driven `DDTraceDeprecationWarning` from `env.py` replaces it.

If `from ddtrace.vendor.debtcollector import deprecate` is now unused in this file, remove it (verify with `grep deprecate ddtrace/contrib/internal/asgi/middleware.py`).

- [ ] **Step 5: Remove the `DD_PYTEST_USE_NEW_PLUGIN_BETA` block in `pytest/_plugin_v2.py`**

In `ddtrace/contrib/internal/pytest/_plugin_v2.py` around line 440, delete:

```python
    if env.get("DD_PYTEST_USE_NEW_PLUGIN_BETA"):
        # Logging the warning at this point ensures it shows up in output regardless of the use of the -s flag.
        deprecate(
            "the DD_PYTEST_USE_NEW_PLUGIN_BETA environment variable is deprecated",
            message="the new pytest plugin is now the default version. No additional configurations are required.",
            removal_version="3.0.0",
            category=DDTraceDeprecationWarning,
        )
```

If `deprecate` and `DDTraceDeprecationWarning` are now unused in this file, remove their imports.

- [ ] **Step 6: Run the failing test from Task 6; verify it now passes**

Run: `scripts/run-tests tests/internal/test_env.py::test_loading_config_with_deprecated_var_fires_warning_exactly_once -v`
Expected: PASS.

- [ ] **Step 7: Run the full env-test file and config-touching tests**

Run: `scripts/run-tests tests/internal/test_env.py tests/tracer/test_settings.py -v`
Expected: PASS.

- [ ] **Step 8: Run lint**

Run: `scripts/lint check`
Expected: clean. If unused-import warnings appear, address them in the same edit.

- [ ] **Step 9: Commit**

```bash
git add ddtrace/internal/settings/_config.py ddtrace/contrib/internal/asgi/middleware.py ddtrace/contrib/internal/pytest/_plugin_v2.py
git commit -m "refactor(deprecations): drop manual deprecate() calls now handled by env registry"
```

---

### Task 8: Sweep for any remaining ad-hoc env var deprecation call sites

**Files:**
- Inspect only; modify as found.

- [ ] **Step 1: Grep for any remaining env var deprecation patterns**

Run:

```bash
grep -rn 'deprecate(' ddtrace/ --include="*.py" -A 4 | grep -B 1 -E '"DD_|"OTEL_|"DATADOG_|"_DD_' | head -40
grep -rn '"DD_[A-Z0-9_]* is deprecated"' ddtrace/ --include="*.py" | head
grep -rn 'log\.warning.*deprecated.*DD_\|log\.warning.*DD_.*deprecated' ddtrace/ --include="*.py" | head
```

- [ ] **Step 2: For each hit, either**

  - Add the corresponding env var to `supported-configurations.json` with full deprecation metadata, regenerate the module (`python scripts/supported_configurations.py`), and delete the manual call site; or
  - Document why the call site must remain (e.g., it fires under a non-env-read condition).

- [ ] **Step 3: If any entries were added in Step 2, regenerate and commit**

```bash
python scripts/supported_configurations.py
git add supported-configurations.json ddtrace/internal/settings/_supported_configurations.py ddtrace/<modified files>
git commit -m "chore(deprecations): sweep remaining ad-hoc env var deprecations into registry"
```

If nothing is found, skip the commit.

---

### Task 9: Release note

**Files:**
- Create: `releasenotes/notes/registry-based-deprecations-*.yaml` (use the `releasenote` skill)

- [ ] **Step 1: Invoke the releasenote skill**

Use the Skill tool with `skill: releasenote`. The skill creates a file in `releasenotes/notes/` following the project conventions.

Provide this content to the skill:

> Add a `features` and `upgrade` entry. `features`: "Deprecation metadata for environment variables now lives in `supported-configurations.json`. Reading a deprecated env var that the user has set emits a single `DDTraceDeprecationWarning` per process, with the `removal_version` and replacement guidance sourced from the registry." `upgrade`: none required.

- [ ] **Step 2: Commit**

```bash
git add releasenotes/notes/registry-based-deprecations-*.yaml
git commit -m "docs(releasenote): add note for registry-based env var deprecations"
```

---

### Task 10: Final validation

**Files:**
- No edits; run-only.

- [ ] **Step 1: Regenerate to confirm clean tree**

Run: `python scripts/supported_configurations.py --check`
Expected: exit 0.

- [ ] **Step 2: Run the relevant test suites**

Run: `scripts/run-tests tests/internal/test_env.py tests/internal/test_supported_configurations_deprecations.py -v`
Expected: PASS.

- [ ] **Step 3: Lint and format**

Run: `scripts/lint format && scripts/lint check`
Expected: clean.

- [ ] **Step 4: Confirm no straggler `print()` calls**

Run: `grep -rn '^\s*print(' ddtrace/internal/settings/env.py scripts/supported_configurations.py`
Expected: no hits in `env.py`; existing print() calls in the generator script (used for user-facing CLI output) are fine.

- [ ] **Step 5: Ready for pre-push review**

Stop here and hand off to the `/pre-push-review` skill before pushing or opening a PR.

---

## Notes for the Engineer

- **`debtcollector.deprecate`** is the existing project standard for user-facing deprecation warnings. Do not switch to `warnings.warn` directly — `deprecate()` formats the message consistently with the rest of the codebase.
- **`DDTraceDeprecationWarning`** is the category. Always pass it explicitly.
- **One-shot semantics**: the `_deprecation_warned` set lives at module level. Tests must clear it (the `reset_warned_keys` fixture does this). In production, the warning fires exactly once per (process, key).
- **Probes vs. reads**: `"X" in env` is a probe and must not fire the warning. `env.get("X")` / `env["X"]` are reads — but the warning only fires if the value is *found* (i.e., the user actually set it). The current `_validate_key` debug-log behavior for deprecated keys remains unchanged.
- **Aliases**: when the user sets a deprecated *alias* and ddtrace reads the *canonical* name (via `CONFIGURATION_ALIASES` fallback), the warning fires referencing the alias the user actually set. This matches the existing `DD_TRACE_INFERRED_SPANS_ENABLED` → `DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED` use case.
- **AIDEV-NOTE** any non-obvious decisions inline (e.g., why `_deprecation_warned` is separate from `_warned_keys`).
- **DO NOT** add new deprecation metadata fields beyond `removal_version`, `extra_message`, and `replaced_by` without coordinating cross-tracer (the schema is shared with dd-trace-go/java/js/dotnet/rb/php).
