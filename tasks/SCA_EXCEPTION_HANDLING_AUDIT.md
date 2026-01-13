# SCA Exception Handling - Security Audit

## 🚨 **CRITICAL FINDINGS**

### ❌ **1. `sca_detection_hook` - NO Exception Handling!**

**File**: `ddtrace/appsec/sca/_instrumenter.py:35-67`

```python
def sca_detection_hook(qualified_name: str) -> None:
    if _registry:
        _registry.record_hit(qualified_name)  # ← Could throw

        span = core.get_span()  # ← Could throw
        if span:
            span._set_tag_str(SCA.TAG_INSTRUMENTED, "true")  # ← Could throw
            span._set_tag_str(SCA.TAG_DETECTION_HIT, "true")  # ← Could throw
            span._set_tag_str(SCA.TAG_TARGET, qualified_name)  # ← Could throw

        telemetry_writer.add_count_metric(...)  # ← Could throw
```

**DANGER**: This hook is injected into **customer code**. If it throws:
- ✅ Customer's function execution stops
- ✅ Exception propagates to customer code
- ✅ **BREAKS PRODUCTION APPLICATIONS!**

**Impact**: **CRITICAL** - Production outage risk

---

### ⚠️ **2. `_sca_detection_callback` - Partial Exception Handling**

**File**: `ddtrace/appsec/sca/_remote_config.py:42-111`

```python
def _sca_detection_callback(payload_list: Sequence) -> None:
    # Lines 68-97: Payload parsing - NO try-except!
    for payload in payload_list:
        content = getattr(payload, "content", None)  # ← Could throw
        metadata = getattr(payload, "metadata", {})  # ← Could throw

        if isinstance(content, dict):  # ← Could throw
            targets = content.get("targets", [])  # ← Could throw

    # Lines 106-109: Has try-except ✅
    try:
        apply_instrumentation_updates(targets_to_add, targets_to_remove)
    except Exception:
        log.error("Failed to apply SCA instrumentation updates", exc_info=True)
```

**DANGER**: Malformed RC payloads could crash RC processing

**Impact**: **HIGH** - RC processing breaks, no more updates

---

### ⚠️ **3. `apply_instrumentation_updates` - No Batch Protection**

**File**: `ddtrace/appsec/sca/_instrumenter.py:154-226`

```python
def apply_instrumentation_updates(targets_to_add: list[str], targets_to_remove: list[str]) -> None:
    # NO top-level try-except

    for target in targets_to_add:
        result = resolver.resolve(target)  # ← If this throws, entire batch fails
        if result:
            success = instrumenter.instrument(qualified_name, func)  # ← Protected internally
```

**DANGER**: One bad target could fail the entire batch

**Impact**: **MEDIUM** - Some targets won't be instrumented

---

### ✅ **4. Individual Operation Protection**

**Good**: `Instrumenter.instrument()` has try-except ✅
**Good**: `SymbolResolver.resolve()` has try-except ✅
**Good**: Registry operations are simple (unlikely to throw) ✅

---

## 📊 **Exception Handling Matrix**

| Component | Has try-except | Returns error | Logs error | Sends telemetry | Status |
|-----------|---------------|---------------|------------|-----------------|--------|
| **`sca_detection_hook`** | ❌ **NO** | N/A | ❌ | ❌ | **CRITICAL** |
| **`_sca_detection_callback`** (parsing) | ❌ **NO** | N/A | ❌ | ❌ | **HIGH** |
| **`_sca_detection_callback`** (apply) | ✅ Yes | N/A | ✅ | ❌ | **GOOD** |
| **`apply_instrumentation_updates`** | ❌ **NO** | N/A | ❌ | ❌ | **MEDIUM** |
| **`Instrumenter.instrument`** | ✅ Yes | ✅ | ✅ | ✅ | **GOOD** |
| **`SymbolResolver.resolve`** | ✅ Yes | ✅ | ✅ | ❌ | **GOOD** |
| **Registry operations** | ⚠️ Partial | N/A | ❌ | ❌ | **OK** |

---

## 🧪 **Test Coverage for Error Cases**

### Current Tests (Checked)

```bash
$ grep -r "def test.*error\|def test.*except\|def test.*fail\|def test.*invalid" tests/appsec/sca/
```

**Results**:
- ❌ **NO tests for hook exceptions**
- ❌ **NO tests for malformed RC payloads**
- ❌ **NO tests for instrumentation failures cascading**
- ❌ **NO tests for registry exceptions**
- ❌ **NO tests for telemetry writer exceptions**
- ❌ **NO tests for span tagging exceptions**

### Missing Test Scenarios

1. **Hook execution errors**:
   - Registry is None when hook is called
   - `core.get_span()` raises exception
   - `span._set_tag_str()` raises exception
   - `telemetry_writer.add_count_metric()` raises exception

2. **RC payload errors**:
   - Malformed payload structure
   - Non-dict content
   - Non-list targets
   - Invalid target format

3. **Instrumentation errors**:
   - `inject_hook()` raises exception
   - Function has no `__code__` attribute
   - Code object is read-only
   - Bytecode injection fails

4. **Resolver errors**:
   - Circular import during resolution
   - Module raises exception on import
   - Symbol access triggers `__getattr__` that fails

5. **Concurrent access errors**:
   - Registry accessed from multiple threads
   - Instrumentation during garbage collection
   - Hook called during module reload

---

## 🔧 **Required Fixes**

### **CRITICAL: Fix #1 - Wrap `sca_detection_hook`**

```python
def sca_detection_hook(qualified_name: str) -> None:
    """Hook injected into instrumented functions.

    MUST NOT throw exceptions - would break customer code!
    """
    try:
        if _registry:
            try:
                _registry.record_hit(qualified_name)
            except Exception:
                # Silent failure - don't break customer code
                pass

            try:
                span = core.get_span()
                if span:
                    span._set_tag_str(SCA.TAG_INSTRUMENTED, "true")
                    span._set_tag_str(SCA.TAG_DETECTION_HIT, "true")
                    span._set_tag_str(SCA.TAG_TARGET, qualified_name)
            except Exception:
                # Silent failure - don't break customer code
                pass

            try:
                telemetry_writer.add_count_metric(
                    TELEMETRY_NAMESPACE.APPSEC,
                    "sca.detection.hook_hits",
                    1,
                    tags=(("target", qualified_name),)
                )
            except Exception:
                # Silent failure - don't break customer code
                pass

    except Exception:
        # Ultimate safety net - NEVER let exceptions escape
        pass
```

**Why**: Hook runs in customer code. ANY exception breaks their application.

---

### **HIGH: Fix #2 - Wrap RC Callback Parsing**

```python
def _sca_detection_callback(payload_list: Sequence) -> None:
    """Process SCA detection RC payloads."""
    try:
        if not payload_list:
            log.debug("Received empty SCA detection payload list")
            return

        targets_to_add = []
        targets_to_remove = []

        for payload in payload_list:
            try:
                log.debug(
                    "Processing SCA detection payload: path=%s, content=%s",
                    getattr(payload, "path", None),
                    bool(getattr(payload, "content", None)),
                )

                content = getattr(payload, "content", None)
                metadata = getattr(payload, "metadata", {})

                if content is None:
                    if isinstance(metadata, dict) and "targets" in metadata:
                        targets = metadata["targets"]
                        if isinstance(targets, list):
                            targets_to_remove.extend(targets)
                else:
                    if isinstance(content, dict):
                        targets = content.get("targets", [])
                        if isinstance(targets, list):
                            targets_to_add.extend(targets)

            except Exception:
                log.error("Failed to process individual payload", exc_info=True)
                # Continue processing other payloads
                continue

        if targets_to_add or targets_to_remove:
            try:
                apply_instrumentation_updates(targets_to_add, targets_to_remove)
            except Exception:
                log.error("Failed to apply SCA instrumentation updates", exc_info=True)

    except Exception:
        log.error("Fatal error in SCA detection callback", exc_info=True)
```

---

### **MEDIUM: Fix #3 - Batch Protection**

```python
def apply_instrumentation_updates(targets_to_add: list[str], targets_to_remove: list[str]) -> None:
    """Apply instrumentation updates from Remote Configuration."""
    try:
        registry = get_global_registry()
        resolver = SymbolResolver()
        lazy_resolver = LazyResolver()
        instrumenter = Instrumenter(registry)

        # Process removals - don't let one failure stop others
        for target in targets_to_remove:
            try:
                log.debug("Processing removal: %s", target)
                instrumenter.uninstrument(target)
                registry.remove_target(target)
            except Exception:
                log.error("Failed to remove target: %s", target, exc_info=True)
                continue  # Keep processing other removals

        # Process additions - don't let one failure stop others
        for target in targets_to_add:
            try:
                if registry.has_target(target):
                    log.debug("Target already tracked: %s", target)
                    continue

                result = resolver.resolve(target)
                if result:
                    qualified_name, func = result
                    registry.add_target(qualified_name, pending=False)
                    success = instrumenter.instrument(qualified_name, func)
                    if success:
                        log.info("Successfully instrumented: %s", qualified_name)
                else:
                    registry.add_target(target, pending=True)
                    lazy_resolver.add_pending(target)

            except Exception:
                log.error("Failed to process target: %s", target, exc_info=True)
                continue  # Keep processing other additions

        # Send telemetry (wrapped for safety)
        try:
            stats = registry.get_stats()
            instrumented_count = sum(1 for s in stats.values() if s["is_instrumented"])
            pending_count = sum(1 for s in stats.values() if s["is_pending"])
            total_count = len(stats)

            telemetry_writer.add_gauge_metric(
                TELEMETRY_NAMESPACE.APPSEC,
                "sca.detection.targets_total",
                total_count
            )
        except Exception:
            log.error("Failed to send telemetry", exc_info=True)

    except Exception:
        log.error("Fatal error in apply_instrumentation_updates", exc_info=True)
```

---

## 🧪 **Required Tests**

### Test File: `tests/appsec/sca/test_error_handling.py`

```python
"""Test error handling and exception safety."""

import pytest
from unittest import mock

def test_hook_handles_registry_none():
    """Hook should not crash if registry is None."""
    # Test that hook doesn't throw when _registry is None

def test_hook_handles_span_exception():
    """Hook should not crash if span tagging fails."""
    # Mock core.get_span() to raise exception

def test_hook_handles_telemetry_exception():
    """Hook should not crash if telemetry fails."""
    # Mock telemetry_writer to raise exception

def test_rc_callback_handles_malformed_payload():
    """RC callback should handle malformed payloads gracefully."""
    # Test with non-dict content, non-list targets, etc.

def test_instrumentation_continues_after_failure():
    """One target failure should not stop batch processing."""
    # Instrument 3 targets, make middle one fail
    # Verify first and third still get instrumented

def test_hook_in_customer_code_never_throws():
    """Integration test: Hook never breaks customer function."""
    # Instrument a function
    # Make ALL hook operations fail
    # Call function
    # Verify it still executes normally
```

---

## 📋 **Implementation Checklist**

- [ ] **CRITICAL**: Wrap `sca_detection_hook` with try-except
- [ ] **HIGH**: Wrap RC callback payload parsing
- [ ] **MEDIUM**: Add per-target try-except in batch processing
- [ ] **TEST**: Add `test_error_handling.py` with 20+ error cases
- [ ] **TEST**: Add integration test for hook exception safety
- [ ] **TEST**: Add stress test for malformed RC payloads
- [ ] **DOCS**: Document exception handling guarantees
- [ ] **REVIEW**: Code review focusing on exception paths

---

## 🎯 **Exception Handling Principles**

1. **Hooks MUST NEVER throw** - They run in customer code
2. **Batch operations should be atomic per-item** - One failure doesn't stop others
3. **RC processing should be resilient** - Bad payloads don't break future updates
4. **All errors should be logged** - For debugging and monitoring
5. **Telemetry should track errors** - For alerting and metrics
6. **Silent failures are acceptable** - Better than breaking customer code

---

## 🔒 **Security Implications**

### Attack Vectors

1. **Malicious RC payload** → Crafted to trigger exceptions → Breaks RC processing
2. **Instrumentation bomb** → Target that causes infinite recursion during resolution
3. **Hook DOS** → Trigger exceptions in every hook call → Performance degradation
4. **Registry corruption** → Concurrent access without proper locking → Undefined behavior

### Mitigations

1. ✅ Add try-except around all hook operations
2. ✅ Validate RC payloads before processing
3. ✅ Add timeout/recursion limits in resolution
4. ✅ Use thread-safe registry operations
5. ✅ Add circuit breaker for repeated failures

---

## 📈 **Monitoring & Observability**

### Telemetry Metrics to Add

1. `sca.detection.hook_errors` - Count of hook exceptions
2. `sca.detection.rc_payload_errors` - Count of malformed payloads
3. `sca.detection.batch_failures` - Count of batch processing failures
4. `sca.detection.target_failures` - Count of individual target failures

### Log Levels

- **ERROR**: Fatal errors that stop processing
- **WARNING**: Individual target failures
- **DEBUG**: Expected errors (module not imported, etc.)

---

## ✅ **Acceptance Criteria**

Before production release:

1. [ ] All hooks wrapped with try-except
2. [ ] All RC processing wrapped with try-except
3. [ ] Batch processing has per-item protection
4. [ ] 100% test coverage for error paths
5. [ ] Integration test confirms no customer code breakage
6. [ ] Stress test with 1000 malformed payloads passes
7. [ ] Performance test shows <1% overhead
8. [ ] Security review approved
