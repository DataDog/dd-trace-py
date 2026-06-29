---

name: add-new-configuration
description: >
  Register a new environment variable / configuration option in dd-trace-py.
  Use whenever you add (or rename) a DD_*/_DD_*/OTEL_*/DATADOG_* environment
  variable so it is documented, validated, and tracked for cross-language
  feature parity. Covers supported-configurations.json, the generated
  _supported_configurations.py module, docs/configuration.rst, and the
  feature-parity registry hand-off.
allowed-tools:
  - Bash
  - Read
  - Edit
  - Grep
  - Glob

---

## When to Use This Skill

Use this skill whenever a code change introduces a **new environment variable**
(or renames/aliases an existing one). In dd-trace-py every `DD_*`, `_DD_*`,
`OTEL_*`, or `DATADOG_*` variable accessed under `ddtrace/` MUST be registered,
or the `supported_configurations` CI check fails.

Typical trigger: you just added something like
`DDConfig.var(bool, AI_GUARD.ENV_OPENAI_ENABLED, default=True)` in a settings
module and need to make CI / docs happy.

---

## Key Principles

1. **`supported-configurations.json` is the source of truth.** Edit it by hand;
   everything else is generated or verified from it.
2. **Never hand-edit `ddtrace/internal/settings/_supported_configurations.py`** —
   it is AUTO-GENERATED. Run the script to regenerate it.
3. **Keep entries alphabetically sorted** within the `supportedConfigurations`
   object (the generator sorts the Python module, but keep the JSON tidy too).
4. **Document user-facing variables** in `docs/configuration.rst` under the
   correct product section.
5. **Hand off to the human** to add the variable to the feature-parity registry —
   this is an external web app the agent cannot edit.

---

## Steps

### 1. Add the entry to `supported-configurations.json`

Find the right alphabetical slot and add a single-element array. Set `type` to
mirror the `DDConfig.var(...)` type. Do NOT infer the `implementation` letter
from neighbouring variables — it is owned by the central Configuration Registry
(see step 6). Use `"A"` for a brand-new key; if the key already exists in the
registry, reuse its letter, and if a maintainer must create a new
implementation version (because the type/default differs from an existing
cross-language entry), reference that version's letter.

```json
"DD_AI_GUARD_OPENAI_ENABLED": [
  {
    "implementation": "A",
    "type": "boolean",
    "default": "true"
  }
],
```

Field notes:
- `type`: one of `boolean`, `string`, `int`, etc. — mirror the `DDConfig.var(...)` type.
- `default`: the **string** form of the default (`"true"`, `"16"`, or `null`
  for no default). Must match the code default exactly.
- `implementation`: the version letter assigned by the central Configuration
  Registry (step 6), NOT inferred from neighbouring vars. A product prefix like
  `DD_TRACE_`/`DD_APPSEC_` legitimately mixes multiple letters, so copying a
  sibling can write the wrong value and only the central CI will catch it.
- Optional keys seen in the registry: `aliases`, `deprecated`, `sensitive`
  (excludes the value from config telemetry). Add these only when applicable.

### 2. Regenerate the Python module

```bash
python scripts/supported_configurations.py
```

This rewrites `ddtrace/internal/settings/_supported_configurations.py`
(`SUPPORTED_CONFIGURATIONS`, `CONFIGURATION_ALIASES`,
`DEPRECATED_CONFIGURATIONS`, `SENSITIVE_CONFIGURATIONS`) and verifies that every
env var accessed in `ddtrace/` is registered.

### 3. Verify everything is in sync

```bash
python scripts/supported_configurations.py --check
```

Expected output:
```
_supported_configurations.py is up to date.
Registry is complete (NNN entries, no unregistered vars).
```

This is the same check CI runs. If it reports unregistered vars, you missed an
entry in step 1.

### 4. Document the variable in `docs/configuration.rst`

Add an entry under the appropriate product heading using the
`.. ddtrace-configuration-options::` directive. Match the surrounding style.

```rst
   DD_AI_GUARD_OPENAI_ENABLED:
     type: Boolean
     default: True
     description: |
       Per-provider kill switch for AI Guard auto-instrumentation of the OpenAI SDK.
       When set to ``False``, disables AI Guard instrumentation for OpenAI only.
```

Include `version_added:` if the option is gated to a specific release.

### 5. Add a release note (if user-impacting)

New public configuration is user-facing, so add a Reno fragment (use the
`releasenote` skill). Skip only for purely internal/private vars.

### 6. Hand off: add it to the feature-parity registry

The agent CANNOT do this — it is an external web application. Tell the user:

> ⚠️ **Action required:** Add this configuration to the cross-language
> feature-parity registry so it is tracked across tracer languages:
> https://feature-parity.us1.prod.dog/#/configurations?viewType=configurations

---

## Validation Checklist

- [ ] Entry added to `supported-configurations.json` (correct type/default/implementation).
- [ ] `python scripts/supported_configurations.py` run (module regenerated).
- [ ] `python scripts/supported_configurations.py --check` passes.
- [ ] Documented in `docs/configuration.rst` under the right section.
- [ ] Release note added (if user-impacting).
- [ ] User reminded to register it at the feature-parity dashboard.

---

## Gotchas

- The `--check` step is what CI enforces; always run it before committing.
- `default` in the JSON is a **string** (or `null`), even for ints/booleans.
- If you only consume the variable in tests or tooling outside `ddtrace/`, the
  completeness check won't force registration — but register it anyway if it is
  a real, documented configuration option.
- Renames: treat the old name as an `alias` rather than deleting it, to avoid
  breaking existing deployments.
