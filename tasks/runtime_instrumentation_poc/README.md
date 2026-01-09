# Runtime Instrumentation PoC

## Overview

This is a proof of concept demonstrating **runtime bytecode patching** of Python functions and methods while a program is running. It showcases dd-trace-py's bytecode instrumentation infrastructure in a clear, educational example.

## Key Features

- **Live Instrumentation**: Patch function bytecode at runtime without restarting the application
- **Visual Dashboard**: Textual-based terminal UI showing real-time call counts and instrumentation status
- **Interactive Control**: Instrument any callable via text input while the app continues running
- **Production Infrastructure**: Uses dd-trace-py's actual `inject_hook()` function (same as production debugging)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                Single Python Process                         │
│                                                               │
│  ┌──────────────────┐        ┌──────────────────────────┐  │
│  │  Target App      │        │  Controller (Textual UI)  │  │
│  │  - Execution Loop│◄───────│  - Live Table Display     │  │
│  │  - Random Calls  │ Reads  │  - Input Handler          │  │
│  └────────┬─────────┘ State  │  - Instrumentation Worker │  │
│           │                   └──────────────────────────┘  │
│           │ Updates                        │                 │
│           ▼                                │ Patches         │
│  ┌────────────────┐                       ▼                 │
│  │  SharedState   │◄────────────── Function.__code__       │
│  │  - Counters    │                                         │
│  │  - Metadata    │                                         │
│  └────────────────┘                                         │
└─────────────────────────────────────────────────────────────┘
```

**Components**:
- **Target App**: Continuously calls dummy functions/methods (~100 calls/second)
- **SharedState**: Thread-safe registry tracking call counts and instrumentation status
- **Instrumenter**: Patches bytecode using dd-trace-py's `inject_hook()` API
- **Textual UI**: Live dashboard with table and input field

## Requirements

- Python 3.11+ (targets Python 3.11 specifically for bytecode compatibility)
- dd-trace-py (you're already in the repo)
- textual >= 0.47.0

## Installation

```bash
# From the PoC directory
pip install -r requirements.txt
```

Or simply:

```bash
pip install 'textual>=0.47.0'
```

## Usage

### Run the PoC

```bash
python tasks/runtime_instrumentation_poc/run.py
```

Or from the PoC directory:

```bash
python run.py
```

### Using the UI

Once running, you'll see a live table showing all instrumentable callables:

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Qualified Name              │ Calls │ Instrumented │ Status              │
├─────────────────────────────────────────────────────────────────────────┤
│ module_a:func_a1            │   145 │            0 │                     │
│ module_a:func_a2            │   132 │            0 │                     │
│ module_a:ClassA1.method_a1  │   128 │            0 │                     │
│ ...                         │   ... │          ... │ ...                 │
└─────────────────────────────────────────────────────────────────────────┘
```

**Keyboard Shortcuts**:
- `i` - Focus input field to instrument a callable
- `q` - Quit the application
- `Esc` - Unfocus input field

**To Instrument a Callable**:

1. Press `i` to focus the input field
2. Enter the full qualified name:
   ```
   tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a1
   ```
3. Press Enter
4. Watch the "Instrumented" counter increase for that callable
5. The "Status" column shows ✓ for instrumented callables

### Example Session

```
# Start the PoC
$ python run.py

# In the UI:
# 1. Press 'i'
# 2. Enter: tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a1
# 3. Press Enter
# 4. See status message: "Successfully instrumented: ..."
# 5. Watch the "Instrumented" counter increase
# 6. Press 'q' to quit
```

## How It Works

### 1. Bytecode Injection

The PoC uses dd-trace-py's `inject_hook()` function to modify function bytecode at runtime:

```python
from ddtrace.internal.bytecode_injection import inject_hook

# Inject hook at function entry
inject_hook(
    func,                    # FunctionType to instrument
    instrumentation_hook,    # Hook callable
    func.__code__.co_firstlineno,  # Line number
    qualified_name          # Arg passed to hook
)
```

For Python 3.11, this injects the following bytecode pattern:

```
push_null
load_const      {hook}
load_const      {arg}
precall         1
call            1
pop_top
```

### 2. Hook Function

The instrumentation hook is called every time an instrumented function executes:

```python
def instrumentation_hook(qualified_name: str) -> None:
    """Called at the entry of instrumented callables."""
    shared_state.increment_instrumentation_count(qualified_name)
```

### 3. Method Instrumentation

For methods, the PoC resolves to the underlying function:

- **Instance methods**: `bound_method.__func__` (all instances share the same `__code__`)
- **Static methods**: Already a function
- **Class methods**: Access `__func__` attribute

### 4. State Management

Thread-safe counters with fine-grained locking:

```python
@dataclass
class CallableInfo:
    call_count: int = 0
    instrumentation_count: int = 0
    lock: Lock = field(default_factory=Lock)  # Per-callable lock
```

## Directory Structure

```
tasks/runtime_instrumentation_poc/
├── README.md                          # This file
├── requirements.txt                   # Dependencies
├── run.py                             # Main entry point
│
├── shared/                            # Shared state
│   ├── __init__.py
│   └── state.py                       # SharedState, CallableInfo
│
├── target_app/                        # Instrumented application
│   ├── __init__.py
│   ├── main.py                        # TargetApp with execution loop
│   ├── registry.py                    # CallableRegistry
│   └── dummy_modules/                 # Dummy functions/classes
│       ├── __init__.py
│       ├── module_a.py
│       ├── module_b.py
│       ├── module_c.py
│       └── module_d.py
│
└── controller/                        # UI and instrumentation
    ├── __init__.py
    ├── ui.py                          # InstrumentationUI (Textual app)
    ├── instrumentation.py             # Instrumenter (bytecode patching)
    └── resolver.py                    # SymbolResolver (path parsing)
```

## Testing

### Phase 1: Basic Execution

Test that call counts are being tracked:

```bash
python tasks/runtime_instrumentation_poc/test_phase1.py
```

Expected output:
```
✓ Phase 1 PASSED: Call counts are increasing!
```

### Phase 2: Bytecode Instrumentation

Test that bytecode patching works:

```bash
python tasks/runtime_instrumentation_poc/test_phase2.py
```

Expected output:
```
✓ Phase 2 PASSED: Bytecode instrumentation works!
```

## Limitations

- **Python Version**: Targets Python 3.11 specifically (bytecode differs across versions)
- **Function Entry Only**: Instruments at function entry, not arbitrary lines
- **No Restoration**: Original bytecode is stored but not currently restored
- **Single Process**: Target app and UI run in the same process

## Educational Value

This PoC demonstrates:

1. **dd-trace-py's bytecode infrastructure**: Uses the same APIs as production debugging
2. **Runtime code modification**: Shows that Python bytecode can be safely modified while running
3. **Async concurrency**: Target app and UI run concurrently using asyncio
4. **Thread safety**: Fine-grained locking for high-performance counter updates

## Future Enhancements

Possible extensions:

- Support for multiple Python versions (3.9-3.14)
- Line-specific instrumentation (not just function entry)
- Bytecode restoration (un-instrument callables)
- Remote instrumentation (separate processes)
- Function arguments capture in hook
- Stack trace capture
- Conditional instrumentation (only instrument if condition met)

## Related dd-trace-py Files

This PoC is built on dd-trace-py's existing infrastructure:

- `/ddtrace/internal/bytecode_injection/__init__.py` - Hook injection API
- `/ddtrace/internal/assembly.py` - Bytecode assembly DSL
- `/ddtrace/debugging/_function/` - Function discovery and patching
- `/.cursor/rules/debugging.mdc` - Comprehensive debugging guide

## License

This PoC is part of dd-trace-py and follows the same license.

## References

- [dd-trace-py debugging documentation](.cursor/rules/debugging.mdc)
- [Python bytecode library](https://github.com/MatthieuDartiailh/bytecode)
- [Textual TUI framework](https://textual.textualize.io/)
