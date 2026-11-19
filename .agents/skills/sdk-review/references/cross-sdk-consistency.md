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
- Flag: code that regenerates or refreshes the runtime ID at runtime for a reason other than fork (for example, an AWS Lambda MicroVM restore), or that stops passing the runtime ID to native components, without citing the shared definition it follows. Remote config and libdatadog treat it as stable per process, so a different need may call for a different identifier.

## 2. Configuration

### XSDK-2.1: Register new settings so the cross-language feature-parity registry tracks them
- Flag: a new `DD_*`/`OTEL_*` setting that skips the [add-new-configuration](../../add-new-configuration/SKILL.md) hand-off, or whose name, default, or accepted values differ from the same setting in another SDK.
