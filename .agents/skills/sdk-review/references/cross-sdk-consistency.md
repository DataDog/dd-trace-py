# Cross-SDK consistency

Behaviour that must match other Datadog SDKs, specs (including OpenTelemetry), and the agent or backend contract.

**When planning:** take the rules below into account when designing the change.

**When reviewing:** report a finding when the diff matches a rule's `Flag:` line, or when it clearly diverges from a spec or another SDK in a way no rule covers (mark it `no rule`), naming the spec, registry, or contract you compared against. Only the cross-SDK reviewer may consult external references.

- Check changes to shared tracer behavior (e.g., context propagation, sampling, configuration, or telemetry) for cross-language impact, using relevant specifications and other tracer implementations as references.
- When an issue stems from the shared design or specification, explicitly flag it as a **design issue** requiring coordination with the affected tracer teams.
- Explain why a Python-only fix is insufficient or would create divergence.
- Distinguish this from a Python implementation bug that can be fixed locally to match the agreed behavior.
- Validation across all dd-trace- repositories is not required.

Other repositories:
- [.NET (`dd-trace-dotnet`)](https://github.com/DataDog/dd-trace-dotnet)
- [Java (`dd-trace-java`)](https://github.com/DataDog/dd-trace-java)
- [Node.js (`dd-trace-js`)](https://github.com/DataDog/dd-trace-js)
- [Go (`dd-trace-go`)](https://github.com/DataDog/dd-trace-go)
- [PHP (`dd-trace-php`)](https://github.com/DataDog/dd-trace-php)
- [Ruby (`dd-trace-rb`)](https://github.com/DataDog/dd-trace-rb)
- [Rust (`dd-trace-rs`)](https://github.com/DataDog/dd-trace-rs)
- [C++ (`dd-trace-cpp`)](https://github.com/DataDog/dd-trace-cpp)
- [Rust (`libdatadog`, provides common shared components)](https://github.com/DataDog/libdatadog)

## 1. Process identity

### XSDK-1.1: Keep the runtime ID stable for the life of a process, changing only on fork
- Flag: a new caller of `_set_runtime_id`/`refresh_identity`, or a new `on_runtime_id_change` subscriber that rebuilds IDs, clients or writers, on any trigger other than a `forksafe` after-fork hook (for example, an AWS Lambda MicroVM restore or an "identity refresh"). Remote config, telemetry and libdatadog treat the runtime ID as stable per process. A non-fork need usually calls for a different identifier.
- ❌ `on_runtime_id_change(client._on_identity_refresh)  # MicroVM restore`
- ✅ keep the runtime ID; add a separate identifier for the restore, agreed with the other SDKs

### XSDK-1.2: Show where native components now get the runtime ID before you stop passing it
- Flag: a removed `runtime_id` argument, or a removed `get_runtime_id()` call, in a native builder or client (for example, `enable_telemetry(...)` or `RemoteConfigClient(...)`), often in a libdatadog bump. Report it whenever the diff doesn't show the replacement source; it's a finding, not a coverage limitation.
- ❌ `builder.enable_telemetry(heartbeat_ms, get_runtime_id(), debug)` → `builder.enable_telemetry(heartbeat_ms, debug)` with no other change
- ✅ the same change, with the PR naming the libdatadog API that now supplies the runtime ID

## 2. Configuration

### XSDK-2.1: Register new settings so the cross-language feature-parity registry tracks them
- Flag: a new `DD_*`/`OTEL_*` setting that skips the [add-new-configuration](../../add-new-configuration/SKILL.md) hand-off, or whose name, default, or accepted values differ from the same setting in another SDK.
