# Migrating an Integration onto the Plugin Interface

Moves an existing, already-working integration from the legacy `PATCH_MODULES`/
`_MODULES_FOR_CONTRIB`/`when_imported` mechanism onto the new
`IntegrationPlugin` interface (`ddtrace/internal/integrations.py`). See the
"Plugin Interface for Tracing Integrations" RFC for the full design and
rationale; this guide is the checklist + edit sequence derived from actually
doing it once, end to end, for `urllib3` (`ddtrace/contrib/internal/urllib3/patch.py`
is the reference — read it alongside this guide).

This is **not** the guide for adding a brand-new integration from scratch —
use [Implementation Guide](implementation-guide.md) for that. Migration only
applies to an integration that already exists and already works; the goal is
zero behavior change, verified by the existing test suite passing unmodified
(aside from the wiring changes below).

## 1. Preconditions — check these before touching anything

- **`trace_handlers.py` entries.** Does `ddtrace/_trace/trace_handlers.py`
  register `core.on(...)` handlers for this integration? Are any of them
  *shared* with another integration (the `wsgi`/`flask`/`django` cluster,
  `botocore` + its sub-services)? Shared handlers need a case-by-case call on
  which integration "owns" them post-migration — don't move a shared handler
  without checking every consumer first.
- **`_MODULES_FOR_CONTRIB` entry.** Does this integration hook more than one
  module (e.g. `elasticsearch`'s eight aliases, `psycopg`'s two)? Each one
  needs its own `ModuleWatchdog` hook inside the new `enable()`.
- **`CONTRIB_DEPENDENCIES` entry.** Does `_monkey.py` force-enable another
  integration alongside this one (e.g. `tornado` → `futures`)? That becomes
  the plugin's own `requires` tuple.
- **Secondary hooks the integration already registers itself.** Does its
  `patch()` already call `wrapt.importer.when_imported(...)` (most
  integrations that self-register secondary hooks) or
  `ModuleWatchdog.after_module_imported(...)` (e.g. `ray`) for its own
  submodules? Those become `enable()`'s own `ModuleWatchdog.register_module_hook()`
  calls — see step 3.
- **Module-scope imports of the target library.** Does `patch.py` do
  `import <library>` at module scope, or only inside `patch()`/`unpatch()`/
  `get_version()`? A module-scope import must move into whichever function
  needs it: the new `enable()` runs the moment `IntegrationRegistry` discovers
  the plugin (via `entry_point.load()`), regardless of whether the target
  library was ever imported by the application — a module-scope import there
  is exactly the force-import Constraint 2 (RFC) prohibits.
- **`_supported_versions()`'s keys.** Are they module names or distribution
  names? They're usually the same string, but not always (`aiobotocore`
  declares both `aiobotocore` and `botocore`, since it needs a compatible
  version of each). The new `supported_versions` dict is keyed by
  *distribution* name — resolved from installed package metadata
  (`ddtrace.internal.packages.get_version_for_package()`), never by importing
  the target module.
- **`tests/contrib/patch.py`'s `PatchTestCase.Base` usage.** Does
  `tests/contrib/{name}/test_{name}_patch.py` subclass it? That harness calls
  `patch()`/`unpatch()`/`get_version()` directly, by name, with zero
  arguments — see step 5 for what replaces it.

## 2. Add the entry point

```toml
# pyproject.toml, alphabetical among the existing ddtrace.integrations entries
[project.entry-points.'ddtrace.integrations']
<name> = "ddtrace.contrib.internal.<name>.patch"
```

`scripts/integration_registry/registry.yaml`'s entry for this integration
(dependency names, tested version range) is unrelated to activation — leave it
as is.

## 3. Rewrite `patch.py`'s module-level surface

Replace `_datadog_patch`, `config._add(...)`, `get_version()`,
`_supported_versions()`, and the zero-argument `patch()`/`unpatch()` pair with:

```python
name = "<name>"
default_enabled = <bool>          # was PATCH_MODULES[name]
requires = (...)                  # was CONTRIB_DEPENDENCIES.get(name, ()) -- omit if empty
supported_versions = {"<dist-name>": "<spec>", ...}   # was _supported_versions()


class <Name>Config(HttpIntegrationConfigMixin, DistributedTracingConfigMixin, IntegrationEnvConfig):
    # only fields this integration doesn't get from the mixins/base
    ...


def enable() -> None:
    ModuleWatchdog.register_module_hook("<target module>", _patch_<target>)
    # one register_module_hook() per module this integration used to hook via
    # _MODULES_FOR_CONTRIB or its own when_imported()/after_module_imported() calls


def disable() -> None:
    ModuleWatchdog.unregister_module_hook("<target module>", _patch_<target>)
    # unregister every hook enable() registered, then unwrap -- see urllib3's own
    # disable() for the sys.modules.get() pattern that avoids a force-import here too
```

Only mix in `HttpIntegrationConfigMixin`/`DistributedTracingConfigMixin`
(`ddtrace/_trace/settings.py`) if the integration actually needs their fields
(HTTP tracing, distributed tracing propagation respectively) — they're
optional, not universal. Bases can be declared in any order;
`IntegrationEnvConfig.__init_subclass__` moves itself to the end of the MRO
automatically. No decorator, no explicit `config = <Name>Config()` line — the
class statement alone constructs and self-registers the instance.

Every actual patch/unwrap function keeps its existing name and body — only
what calls it, and how the target module is obtained, changes. A function
that used to receive the module via a fresh `import <library>` now receives it
as the `ModuleWatchdog` hook's own argument (a `ModuleType`), the same value
`enable()`'s registered callback is invoked with.

If `get_version()` and `supported_versions` (or the legacy `_supported_versions()`)
aren't read by anything else in this integration's own code, don't keep a
`get_version()` function at all — nothing in the new machinery calls it;
`enable_plugin()` resolves the installed version itself, straight from
`supported_versions`, via `get_version_for_package()`.

## 4. Remove the legacy registration

- Delete this integration's entries from `PATCH_MODULES`,
  `_MODULES_FOR_CONTRIB`, and `CONTRIB_DEPENDENCIES` in `ddtrace/_monkey.py`.
- Move any `trace_handlers.py` registrations for this integration into
  `enable()`'s own hook callback(s), called once the target module actually
  imports — same idempotency guarantee as today (`core.on(...)` registration
  is additive and safe to move later in the timeline, never earlier).

Nothing else in `_monkey.py`/`IntegrationRegistry` needs to change:
`_patch_all()`/`patch()` already consult the registry for any contrib name it
resolves to a plugin, falling through to the legacy path for everything else.

## 5. Rewrite the tests

Delete `tests/contrib/{name}/test_{name}_patch.py` if it's a
`PatchTestCase.Base`-derived generic suite — that harness re-proves
import-ordering/idempotency behavior that now belongs to the plugin machinery
itself, tested once in `tests/internal/test_integrations.py` against synthetic
fake plugins, not per integration.

In `tests/contrib/{name}/test_{name}.py`:

- Point `setUp()`/`tearDown()` at the registry instead of calling `enable()`/
  `disable()` directly:

  ```python
  from ddtrace.contrib.internal.<name> import patch as <name>_patch
  from ddtrace.internal.integrations import registry as _integration_registry

  def setUp(self):
      super().setUp()
      _integration_registry.enable_plugin(<name>_patch)

  def tearDown(self):
      super().tearDown()
      _integration_registry.disable_plugin(<name>_patch)
  ```

  Going through the registry (not calling `plugin.enable()` directly) is what
  makes a second `enable_plugin()` call in the same test process a no-op
  instead of double-wrapping — see `test_double_enable` in
  `tests/contrib/urllib3/test_urllib3.py` for the pattern that exercises this.
- Add/keep a couple of lines confirming `supported_versions` contains real,
  correct data — not a generic harness, just `assert "<dist>" in
  supported_versions`.
- Add one end-to-end smoke test that runs `ddtrace-run` in a subprocess with
  `DD_TRACE_<NAME>_ENABLED=1` and asserts the target library isn't in
  `sys.modules` until the script itself imports it, then that the wrapped
  method actually got wrapped (`ddtrace.internal.compat.is_wrapted`/
  `ddtrace.internal.wrapping.is_wrapped`). Copy
  `test_ddtrace_run_enable_on_import` from `tests/contrib/urllib3/test_urllib3.py`
  and adjust the env var / import / assertion. This is the one thing no
  synthetic-plugin unit test can cover: whether *this* integration's real
  `pyproject.toml` entry point, registry discovery, and env-var wiring are
  correct together.

## 6. Verify

- **`run-tests` skill**: run this integration's own suite, plus
  `tests/internal/test_integrations.py` and `tests/tracer/test_monkey.py`
  (the legacy-path fallback and env-var-override logic live there).
- **`lint` skill**: format + typecheck every file touched.
- `python scripts/supported_configurations.py --check`: if the new config
  class declares any `envier` field, this confirms it resolves to its real,
  already-registered `DD_<NAME>_<FIELD>` env var name — the scanner already
  understands `IntegrationEnvConfig`'s dynamically-derived prefix and its
  cross-file mixins (`ddtrace/_trace/settings.py`), so this should just pass;
  if it reports a *bare*, unprefixed field name instead, something about the
  class's bases doesn't match what the scanner expects (see
  `scripts/supported_configurations.py`'s `_integration_plugin_prefix()`/
  `_resolve_base_class()`).
- **A/B comparison for anything touching shared files** (`_monkey.py`,
  `debug.py`): `git stash` your changes, run the broader `tracer` suite, note
  the pass/fail count, `git stash pop`, re-run, and confirm the counts match —
  this is how you tell a pre-existing flaky/broken test apart from an actual
  regression, without having to triage every failure by hand.
