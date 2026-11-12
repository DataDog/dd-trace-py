# Module bridges to extracted packages

Modules extracted from dd-trace-py into
[datadog-apm-python](https://github.com/DataDog/datadog-apm-python) are replaced
in this repository by bridges: modules that only re-import the extracted
``_ddtrace_internal*`` package (see the `bridge-extracted-module` skill for the
workflow and `scripts/package-split make-bridge` / `scripts/lint
bridged-modules` for the tooling).

| Module | Extracted package | Version | Bridge PR | Status |
|---|---|---|---|---|
| `ddtrace.internal.codeowners` | `ddtrace-internal-base` (`_ddtrace_internal_base.codeowners`) | 0.1.0 | #21725 | draft |

Candidates for the same pattern (no bridge yet):

- `ddtrace/internal/forksafe.py`
- `ddtrace/internal/utils/w3c.py` (W3C tracestate)
- OTel sampling propagation
