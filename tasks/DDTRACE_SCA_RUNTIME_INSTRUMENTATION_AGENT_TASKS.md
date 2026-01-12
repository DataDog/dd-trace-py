# Runtime Function Instrumentation for SCA via Remote Configuration

## Task Specification for AI Agent

### Context

We want to evolve a runtime Python bytecode instrumentation proof of concept into a production-oriented design integrated into `ddtrace`, specifically under:

ddtrace/appsec/sca/

The goal is to enable dynamic, runtime instrumentation of arbitrary customer code, controlled by:

- A feature flag (environment variable)
- Remote Configuration (RC) updates received while the application is running

This system must operate in-process, in-memory, and without requiring application restarts.

---

## High-Level Objective

Design and structure a system such that:

- When the environment variable DD_SCA_DETECTION_ENABLED=1 is set,
- ddtrace can instrument arbitrary Python functions and methods in customer code at runtime,
- Instrumentation targets are driven by Remote Configuration payloads that arrive asynchronously and may change over time.

---

## Core Requirements

### 1. Feature Gating

Instrumentation logic must be fully disabled by default.

Activation conditions:

- DD_SCA_DETECTION_ENABLED=1 must be present
- AppSec + SCA must already be enabled

---

### 2. Location in Codebase

All new logic must live under:

ddtrace/appsec/sca/

Suggested structure:

ddtrace/appsec/sca/
  config.py
  remote_config.py
  resolver.py
  instrumenter/
    patcher.py
    registry.py
  runtime.py

---

### 3. Remote Configuration Integration

Remote Configuration is the source of truth for what to instrument.

The system must:

- Subscribe to existing ddtrace RC mechanisms
- Handle updates arriving at startup or during runtime
- Support incremental updates

Example RC payload:

{
  "sca_instrumentation": {
    "targets": [
      "module.submodule:function",
      "package.module:Class.method"
    ]
  }
}

---

### 4. Runtime Symbol Resolution

For each target received via RC:

1. Resolve the string to a Python callable
2. Validate that it is instrumentable (pure Python)
3. Generate a canonical internal identifier

Resolution must support late imports and repeated updates.

---

### 5. Instrumentation Strategy

Instrumentation must be:

- Runtime-only
- In-memory
- Idempotent
- Preferably reversible

Prefer bytecode or code-object patching over wrappers.

---

### 6. Lifecycle & Concurrency Model

The system must:

- Initialize lazily
- Apply instrumentation asynchronously
- Avoid blocking application threads

---

### 7. State Management

Track:

- Instrumented functions
- Original code objects
- Instrumentation metadata

State must be thread-safe and bounded.

---

### 8. Failure Handling & Safety

Failures must be non-fatal and never crash the application.

---

### 9. Observability

Include debug logging and internal counters.

---

## Deliverables

- Proposed architecture
- Execution flow
- Pseudocode or skeletons
- Assumptions and limitations
- Risk analysis

---

## Summary

Design a gated, remote-config-driven runtime instrumentation system for Python inside ddtrace/appsec/sca that dynamically instruments customer code using bytecode or code-object patching, without restarting the application.
