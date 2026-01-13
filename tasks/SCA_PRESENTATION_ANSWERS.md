# SCA Runtime Instrumentation: Technical Presentation Answers

## 1. Application Startup Impact

**Answer: No impact on application startup time.**

### Why?

The SCA runtime instrumentation system is designed with **lazy initialization** and **feature gating** to ensure zero startup overhead:

#### Feature Gating
- The system is **completely disabled by default**
- Activation requires **both** environment variables:
  - `DD_APPSEC_SCA_ENABLED=1` (main SCA product opt-in)
  - `DD_SCA_DETECTION_ENABLED=1` (runtime detection feature)
- If either flag is missing, no code is loaded or initialized

#### Lazy Initialization
When enabled, the startup sequence involves only:
1. **Registry creation**: Instantiates an empty `InstrumentationRegistry` (just a dictionary with a lock)
2. **Remote Configuration subscription**: Registers a callback with the existing RC worker thread
3. **No instrumentation at startup**: Zero functions are instrumented until RC sends targets

#### Deferred Instrumentation
The key architectural decision is that **instrumentation happens asynchronously**:
- The system subscribes to Remote Configuration but performs no symbol resolution or bytecode patching at startup
- Instrumentation only occurs when:
  - RC worker receives a payload (happens in RC worker thread, not main thread)
  - Targets are resolved and patched asynchronously
  - No blocking of application initialization or request handling

This design ensures the application starts at full speed, with instrumentation applied progressively as directed by Remote Configuration.

---

## 2. Why Bytecode Instrumentation (Debugger-style)?

**Answer: Bytecode instrumentation enables runtime dynamism, in-memory modification, and safe concurrent updates.**

### Core Properties

#### In-Memory, Runtime Modification
- Bytecode patching modifies the `__code__` attribute of function objects **in-memory**
- No file system writes, no source code changes, no compilation artifacts
- Changes are **immediate** and affect the running process

#### Non-Invasive Injection
- Uses dd-trace-py's `inject_hook()` API from `ddtrace/internal/bytecode_injection`
- Converts function bytecode to abstract representation (using `bytecode` library)
- Injects hook call at target line (typically first line)
- Converts back to concrete code object and assigns to `func.__code__`

#### Idempotent and Reversible (in principle)
- Instrumentation is idempotent: instrumenting the same function twice is safe (registry tracks state)
- Original `__code__` objects are preserved in the registry
- Uninstrumentation is theoretically possible by restoring the original code object (though `eject_hook()` would be cleaner)

#### Shared with Debugger
The same bytecode injection infrastructure is used by:
- **Dynamic Instrumentation (Debugger)**: For adding log lines and metrics at runtime
- **SCA Detection**: For tracking vulnerable function invocations

This reuse provides:
- Battle-tested infrastructure
- Consistent behavior across features
- Lower maintenance burden

### Why This Approach Fits SCA Detection

- **Targeted instrumentation**: Only instrument specific functions identified as vulnerable
- **Dynamic control**: Enable/disable instrumentation without restart
- **Low overhead**: Hook injection is lightweight; only targeted functions pay the cost
- **Observable**: Each hook execution is tracked, tagged in spans, and reported via telemetry

---

## 3. Why Bytecode Instead of AST Patching (as used in IAST)?

**Answer: AST patching operates at import-time and is static; bytecode patching operates at runtime and is dynamic.**

### Fundamental Differences

| Aspect               | **AST Patching (IAST)**                   | **Bytecode Patching (SCA)**        |
|----------------------|-------------------------------------------|------------------------------------|
| **When**             |  Import-time                              | Runtime                            |
| **Trigger**          | Module import hook                        | Remote Configuration update        |
| **Target**           | Modify AST before compilation             | Modify compiled code object        |
| **Scope**            | All matching patterns in imported modules | Specific functions from RC payload |
| **Restart Required** | Yes (to re-import modules)                | No                                 |
| **Dynamic Updates**  | Not possible (static instrumentation)     | Core feature (RC-driven)           |

### Why IAST Uses AST Patching

IAST (Interactive Application Security Testing) needs to:
- Instrument **broad categories** of functions (e.g., all SQL calls, all file operations)
- Apply instrumentation **deterministically** at import time
- Propagate taint through the entire application from the start

This is a **static, broad-spectrum** approach: instrument everything that *might* be relevant.

### Why SCA Uses Bytecode Patching

SCA Detection needs to:
- Instrument **specific functions** identified as vulnerable (e.g., `vulnerable_lib.module:CVE_12345_function`)
- Apply instrumentation **dynamically** based on Remote Configuration
- **Add or remove** targets while the application is running
- Support **late-arriving targets** (modules imported after RC payload received)

This is a **dynamic, targeted** approach: instrument only what Remote Configuration tells us to.

### Interaction with Remote Configuration

#### AST Patching + Remote Config
- **Problem**: Modules are already imported when RC arrives
- **Solution**: Would require module reloading or restart (fragile and disruptive)

#### Bytecode Patching + Remote Config
- **Solution**: RC worker thread receives payload → resolves symbols → patches bytecode
- Works for:
  - Already-imported modules (patch in-memory code objects)
  - Not-yet-imported modules (lazy resolution via `LazyResolver`)
  - Incremental updates (add/remove targets without restart)

### Ability to Modify Already-Running Applications

**This is the killer feature.**

AST patching cannot modify code that has already been imported and is executing. Bytecode patching can:
1. RC sends new target: `critical_lib.auth:check_password`
2. Resolver finds the function object (already imported hours ago)
3. Instrumenter patches `check_password.__code__` in-memory
4. Next invocation of `check_password` (by any thread) executes the hook
5. **No restart, no downtime, no disruption**

This enables:
- **Emergency response**: Instrument a newly-discovered vulnerability immediately
- **Gradual rollout**: Enable detection for specific functions across a fleet
- **A/B testing**: Instrument different targets in different environments

---

## 4. Support for Asynchronous Functions

**Answer: Yes, bytecode instrumentation works correctly for `async def` functions.**

### Why It Works

#### Async Functions Are Functions
In Python, `async def` defines a function that returns a **coroutine object** when called:

```python
async def fetch_data(url):
    # function body
    pass

# Calling fetch_data() returns a coroutine, does not execute the body
coro = fetch_data("http://example.com")
# The body executes when the coroutine is awaited
await coro
```

#### Bytecode Injection Happens at Function Entry
The SCA hook is injected at the **first line** of the function:
- For regular functions: Hook runs before the function body
- For async functions: Hook runs before the coroutine body

The hook itself is a **synchronous** function that:
1. Records the hit in the registry
2. Adds span tags
3. Sends telemetry
4. Returns immediately

#### Interaction with Coroutine Creation

When an `async def` function is called:
1. **Synchronous part**: Python creates the coroutine object (this is where the injected hook runs)
2. **Asynchronous part**: The coroutine body executes when awaited

The injected hook runs during step (1), before any `await` semantics come into play. This is safe and non-blocking.

#### Bytecode is Function-Type Agnostic

The `inject_hook()` API operates on `FunctionType` objects and their `__code__` attributes:
- Regular functions: `function.__code__` is a code object
- Async functions: `async_function.__code__` is also a code object (with `CO_COROUTINE` flag)
- The bytecode injection mechanism doesn't care about the flag; it injects instructions the same way

### Implications

- **No special handling needed**: Async functions are instrumented identically to regular functions
- **No async/await in hook**: The hook is synchronous, which is correct (we're detecting function invocation, not awaiting results)
- **Coroutine tracking**: We detect when the async function is *called*, not when it completes (which is the desired behavior for vulnerability detection)

### Example

```python
# Original async function
async def vulnerable_query(user_input):
    result = await db.execute(f"SELECT * FROM users WHERE id={user_input}")
    return result

# After instrumentation (conceptually)
async def vulnerable_query(user_input):
    sca_detection_hook("myapp.db:vulnerable_query")  # ← Injected (synchronous)
    result = await db.execute(f"SELECT * FROM users WHERE id={user_input}")
    return result

# Usage (unchanged)
coro = vulnerable_query("123")  # Hook runs here (synchronous)
result = await coro              # Async execution continues
```

The hook fires when `vulnerable_query()` is called (coroutine creation), not during `await`. This is exactly what we want: detect that vulnerable code is being invoked.

---

## 5. End-to-End Runtime Flow

**Answer: The system operates via a multi-threaded architecture with a dedicated Remote Configuration worker thread that asynchronously applies instrumentation to application code.**

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Application Main Thread(s)                                  │
│  - Handling requests                                         │
│  - Executing business logic                                  │
│  - Calling instrumented functions (when hooks are injected)  │
└─────────────────────────────────────────────────────────────┘
                           │
                           │ (reads func.__code__)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Instrumented Functions                                      │
│  - func.__code__ contains injected hook bytecode             │
│  - Hook executes on every call                               │
│  - Hook updates registry and sends telemetry                 │
└─────────────────────────────────────────────────────────────┘
                           ▲
                           │ (writes func.__code__)
                           │
┌─────────────────────────────────────────────────────────────┐
│  Remote Configuration Worker Thread                          │z
│  (RemoteConfigPoller - periodic background thread)           │
│  - Polls agent for RC payloads                               │
│  - Invokes product callbacks when payloads arrive            │
│  - Runs independently of application threads                 │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  SCA Detection Callback (_sca_detection_callback)           │
│  - Parses RC payload (list of target functions)              │
│  - Calls apply_instrumentation_updates()                     │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Symbol Resolution & Instrumentation                         │
│  1. SymbolResolver.resolve() - string → function object      │
│  2. If not found → LazyResolver (defer until module loads)   │
│  3. If found → Instrumenter.instrument()                     │
│     - Store original __code__                                │
│     - inject_hook() modifies func.__code__                   │
│     - Update registry state                                  │
└─────────────────────────────────────────────────────────────┘
```

### Detailed Flow

#### Step 1: Remote Configuration Payload Arrival

**Remote Configuration Worker Thread** (background, periodic):
- Runs as a `PeriodicService` (inherits from threading-based service)
- Polls agent every `DD_REMOTE_CONFIG_POLL_INTERVAL` seconds (default: 5s)
- When new configuration arrives, invokes registered callbacks

**Payload Structure**:
```json
{
  "targets": [
    "myapp.auth:verify_token",
    "django.contrib.auth:authenticate",
    "vulnerable_lib.module:unsafe_function"
  ]
}
```

#### Step 2: Callback Invocation (RC Worker Thread)

The `_sca_detection_callback` is invoked **in the RC worker thread** (not application thread):

1. **Parse payload**: Extract list of target function names
2. **Compute delta**: Determine which targets to add/remove
3. **Call `apply_instrumentation_updates(targets_to_add, targets_to_remove)`**

This happens **asynchronously** relative to application request handling.

#### Step 3: Symbol Resolution (RC Worker Thread)

For each target in `targets_to_add`:

**Attempt Resolution**:
```python
result = SymbolResolver.resolve("myapp.auth:verify_token")
```

**Case A: Module Already Imported**
- `importlib.import_module("myapp.auth")` succeeds
- Navigate to symbol: `getattr(module, "verify_token")`
- Extract `FunctionType` from callable (handles methods, staticmethods, etc.)
- **Result**: `("myapp.auth:verify_token", <function verify_token>)`

**Case B: Module Not Yet Imported**
- `importlib.import_module("myapp.auth")` raises `ImportError`
- Add to `LazyResolver` pending set
- Mark as pending in registry
- **Result**: `None` (deferred instrumentation)

#### Step 4: Bytecode Modification (RC Worker Thread)

For successfully resolved targets:

```python
instrumenter.instrument(qualified_name, func)
```

**Instrumentation Steps**:
1. **Check registry**: Skip if already instrumented
2. **Store original code**: `original_code = func.__code__`
3. **Inject hook**:
   ```python
   inject_hook(func, sca_detection_hook, first_line, qualified_name)
   ```
   - Converts `func.__code__` to abstract bytecode (using `bytecode` library)
   - Injects bytecode sequence:
     ```
     LOAD_CONST    sca_detection_hook
     PUSH_NULL
     LOAD_CONST    "myapp.auth:verify_token"
     CALL          1
     POP_TOP
     ```
   - Converts back to concrete code object
   - **Atomic assignment**: `func.__code__ = new_code`
4. **Update registry**: Mark as instrumented, store original code

#### Step 5: Thread Safety and Concurrency

**Key Question**: What if an application thread is executing `verify_token` while we're patching it?

**Answer**: Python's code object assignment is atomic at the C level:
- `func.__code__ = new_code` is a single pointer swap in CPython
- Threads already executing the old code continue with the old bytecode (their frame has a reference to the old code object)
- New invocations use the new code object
- **No crashes, no race conditions, no memory corruption**

**Registry Locking**:
- `InstrumentationRegistry` uses thread-safe locks (`threading.Lock`)
- All state updates are protected
- Hit counting is thread-safe

#### Step 6: Hook Execution (Application Threads)

When an instrumented function is called by **any application thread**:

1. **Hook runs** (injected bytecode at function entry):
   ```python
   sca_detection_hook("myapp.auth:verify_token")
   ```

2. **Hook implementation**:
   - `registry.record_hit(qualified_name)` - increment counter (thread-safe)
   - Add span tags: `span._set_tag_str("_dd.sca.instrumented", "true")`
   - Send telemetry: `telemetry_writer.add_count_metric("sca.detection.hook_hits", 1)`

3. **Function continues normally**: Hook returns, function body executes

**Performance**: Hook overhead is minimal (dictionary lookup, span tag, telemetry queue write).

#### Step 7: Lazy Resolution (Future Module Imports)

**Not yet fully implemented**, but the design supports:

When a module is imported (via `ModuleWatchdog` or import hooks):
1. Check `LazyResolver.get_pending()` for targets in this module
2. Retry resolution: `SymbolResolver.resolve(pending_target)`
3. If successful, instrument immediately
4. Remove from pending set

This ensures targets are instrumented **eventually**, even if specified before the module is loaded.

### Concurrency Safety Summary

| Operation                 | Thread              | Safety Mechanism                         |
|---------------------------|---------------------|------------------------------------------|
| **RC payload processing** | RC worker thread    | Isolated from app threads                |
| **Symbol resolution**     | RC worker thread    | Read-only on application modules         |
| **Bytecode modification** | RC worker thread    | Atomic `func.__code__` assignment        |
| **Registry updates**      | RC worker thread    | `threading.Lock`                         |
| **Hook execution**        | Application threads | Read-only on registry (during execution) |
| **Hit counting**          | Application threads | Per-state `threading.Lock`               |

### Why This Is Safe and Non-Disruptive

1. **No blocking**: RC worker operates independently; application threads never wait for instrumentation
2. **Atomic updates**: Code object replacement is atomic; no partial state
3. **Graceful degradation**: If symbol resolution or instrumentation fails, it's logged and skipped (application unaffected)
4. **Thread-safe state**: All shared state (registry) is protected by locks
5. **No restarts**: Instrumentation applies to running code without downtime

---

## Summary

The SCA runtime instrumentation system achieves **dynamic, remote-controlled vulnerability detection** through:

1. **Zero startup impact** via lazy initialization and feature gating
2. **Bytecode instrumentation** for in-memory, runtime modification
3. **Dynamic updates** impossible with AST patching, enabled by bytecode patching
4. **Async function support** via function-type-agnostic bytecode injection
5. **Safe concurrent operation** via RC worker thread, atomic code updates, and thread-safe state management

This architecture enables **production-safe, zero-downtime instrumentation** controlled entirely by Remote Configuration, making it suitable for enterprise-scale vulnerability detection and response.
