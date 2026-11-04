# New integration checklist

Paths are repository-relative. Read the closest current reference implementation rather than
copying a synthetic patch module. For a small edit to an existing integration, apply only the
steps affected by the change.

## Patch and configuration

- Implement `patch()`, `unpatch()`, `get_version()`, and `_supported_versions()` in
  `ddtrace/contrib/internal/<name>/patch.py`; inspect the current return types in the reference.
  `_supported_versions()` returns a dependency-to-version mapping, not a string.
- Put the Sphinx integration docstring in `__init__.py`; do not treat that private package as a
  public re-export of patch functions. Users enable integrations through `ddtrace.patch()`.
- Register config at module scope with `config._add()`. Add the integration to `INTEGRATION_CONFIGS`
  in `ddtrace/internal/settings/_config.py`, or its settings will be ignored. Place new entries
  alphabetically rather than appending them to the end.
- Add `PATCH_MODULES` in `ddtrace/_monkey.py`. Add `_MODULES_FOR_CONTRIB` when dependency import names
  differ from the contrib name, such as `mysqldb` importing `MySQLdb`. Place new `PATCH_MODULES`
  entries alphabetically; avoid reordering unrelated entries.
- Add dependency metadata to `scripts/integration_registry/registry.yaml` and
  `ddtrace/contrib/internal/<name>/<name>.manifest.yaml`. Follow the manifest section of
  [contributing-integrations](../../../../docs/contributing-integrations.rst), including matching package names.

## LLMObs additions

Use the [LLMObs implementation guide](../../llmobs-integrations/references/implementation-guide.md)
for event/span ownership and extraction. Register new exported integration classes in
`_INTEGRATION_MODULES` in `ddtrace/llmobs/_integrations/__init__.py`; `__all__` is derived from
that lazy mapping. Do not replace lazy registration with eager imports of every integration.
Connect standard request integrations through `SpanLifecycleEvents` subscribers under
`ddtrace/llmobs/_contrib/<name>/`, registered through the `ddtrace.integration_hooks` entry point.
Follow `ddtrace/llmobs/_contrib/anthropic/` and the LLMObs guide so contrib does not import LLMObs.

## Tests and documentation

- Add component, suite, and matrix entries to the appropriate suitespec: `tests/contrib/suitespec.yml`
  for standard integrations, `tests/llmobs/suitespec.yml` for LLM/AI integrations.
- Match a current suite's schema and dependency variants. Follow supported-version policy in the
  contributor guide; generate locks with `scripts/test-requirements lock <environment-name>`.
  A matrix variant's `name` is the exact environment name accepted by that command, not the suite
  label or a `run-tests --venv` hash. Use the integration name as the base, adding a qualifier when
  needed; multiple dependency/Python variants can share a name. The matrix `command` selects the
  test directory. See the `httpx` suite and [test schema](../../../../tests/README.md).
  Commit updated definitions and locks together; see [lock maintenance](../../../../docs/contributing-testing.rst)
  for missing/obsolete locks and pruning.
- Test patch/unpatch behavior and the affected instrumentation, including errors and applicable
  sync/async or streaming paths. Prefer existing snapshot fixtures for APM span assertions.
  The usual layout is `tests/contrib/<name>/test_<name>_patch.py` for patch cycles and
  `test_<name>.py` for APM behavior. Use `tests/contrib/anthropic/test_anthropic_patch.py` for
  patch/unpatch and double-patch assertions, and `tests/contrib/httpx/test_httpx.py` for snapshots.
  Keep an existing suite's more specific split when extending it. Use `@pytest.mark.snapshot`
  where the suite has snapshot support, rather than replacing its fixtures.
- Use the [LLMObs testing guide](../../llmobs-integrations/references/testing-guide.md) for LLMObs assertions
  and transport fixtures. Only add vcrpy where the suite uses it.
- Reference the integration's `__init__.py` docstring through an `automodule` entry in
  `docs/integrations.rst` where applicable, in alphabetical order. Update `docs/index.rst` when
  adding an integration there. Use `releasenote` for the customer-facing change.
- Use `run-tests` and `lint` for execution, formatting, and type validation.

## OpenTelemetry HTTP semantics

Shared HTTP span attributes live in ddtrace/_trace/otel/http/tags.py; resource
naming and ownership live in ddtrace/_trace/otel/http/resource.py.
set_http_meta writes the OTel attributes through _set_http_meta_otel when the
semantics flag is enabled, and the Datadog ones through _set_http_meta
otherwise. Preserve user resource replacements with the resource helpers.
