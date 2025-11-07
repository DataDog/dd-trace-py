# IAST Fork Safety Solution

## Problem

IAST was causing segmentation faults in multiprocessing scenarios where `multiprocessing.Process` would fork after IAST had already initialized native extension state. However, completely disabling IAST in all forked children broke web framework workers (gunicorn, uvicorn, Django, Flask) which fork early and need IAST coverage.

## Solution: Conditional Fork Behavior

The fork handler now **differentiates between two types of forks**:

### 1. Early Forks (Web Framework Workers) ✅ IAST Enabled

**When:** Fork occurs BEFORE IAST has any active request contexts  
**Examples:** gunicorn workers, uvicorn workers, Django workers  
**Behavior:** IAST remains enabled in the child  
**Why safe:** No native state exists yet - IAST initializes fresh in the worker  

```python
# Web worker startup sequence:
# 1. Master process loads ddtrace
# 2. Master process registers fork handler
# 3. Master process forks workers (NO active IAST context yet)
# 4. Worker processes initialize IAST fresh ✅
```

### 2. Late Forks (Multiprocessing) ⚠️ IAST Disabled

**When:** Fork occurs AFTER IAST has active request contexts  
**Examples:** `multiprocessing.Process`, `os.fork()` during request handling  
**Behavior:** IAST is disabled in the child  
**Why necessary:** Native state is corrupted - would cause segfaults  

```python
# Multiprocessing sequence:
# 1. Parent process handles requests with IAST enabled
# 2. Parent has active IAST contexts with native state
# 3. Parent forks multiprocessing.Process (active context exists)
# 4. Child inherits corrupted native state
# 5. Fork handler detects active context → disables IAST ⚠️
```

## Detection Logic

```python
def _disable_iast_after_fork():
    current_context = IAST_CONTEXT.get(None)
    
    if current_context is None:
        # Early fork: No active context
        # → Keep IAST enabled (web worker)
        return
    
    # Late fork: Active context exists
    # → Disable IAST (multiprocessing)
    clear_all_request_context_slots()
    IAST_CONTEXT.set(None)
    asm_config._iast_enabled = False
```

## Benefits

✅ **Web workers work correctly** - IAST provides full coverage  
✅ **Multiprocessing doesn't crash** - IAST safely disabled  
✅ **Automatic detection** - No manual configuration needed  
✅ **Minimal overhead** - Simple context check  

## Test Coverage

### Early Fork Tests
- `test_early_fork_keeps_iast_enabled()` - Unit test
- `test_iast_header_injection_secure()` - Integration test with Flask workers
- All `test_*_testagent.py` tests - Real web framework scenarios

### Late Fork Tests
- `test_multiprocessing_with_iast_no_segfault()` - Unit test
- `test_fork_with_os_fork_no_segfault()` - Direct fork test
- `test_subprocess_has_tracer_running_and_iast_env()` - Multiprocessing test

## Implementation Details

### Files Modified

1. **`ddtrace/appsec/_iast/__init__.py`**
   - `_disable_iast_after_fork()` - Conditional fork handler
   - Detection logic based on `IAST_CONTEXT.get(None)`

2. **Test files**
   - Updated docstrings to explain early vs late forks
   - Added `test_early_fork_keeps_iast_enabled()` test

### Key Code Locations

- Fork handler registration: `ddtrace/appsec/_iast/__init__.py:_register_fork_handler()`
- Fork detection logic: `ddtrace/appsec/_iast/__init__.py:_disable_iast_after_fork()`
- Context type: `ddtrace/appsec/_iast/_iast_request_context_base.py:IAST_CONTEXT`

## Debugging

To debug fork behavior, enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Look for these log messages:

- `"IAST fork handler: No active context, keeping IAST enabled (web worker fork)"` → Early fork ✅
- `"IAST fork handler: Active context detected, disabling IAST (multiprocessing fork)"` → Late fork ⚠️

## Known Limitations

1. **No IAST coverage in multiprocessing children**: This is by design for safety
2. **Context-based detection**: Relies on `IAST_CONTEXT` being set during active requests
3. **Web workers must fork early**: Workers that fork after handling requests will have IAST disabled

## Migration Notes

If you were experiencing:
- ✅ **Segfaults with multiprocessing** → Fixed! IAST auto-disables
- ✅ **Missing IAST data in web workers** → Fixed! IAST stays enabled
- ⚠️ **IAST disabled after manual fork during request** → Expected behavior for safety

## Future Improvements

Possible enhancements:
1. Better detection for framework-specific fork patterns
2. Per-worker IAST enable/disable configuration
3. Safe native state reconstruction for late forks (complex, risky)
