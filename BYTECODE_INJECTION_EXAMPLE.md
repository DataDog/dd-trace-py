# Bytecode Injection Example

This document explains the minimal example of using `ddtrace.internal.bytecode_injection.inject_hook`.

## Quick Start

Run the example:
```bash
python example_bytecode_injection.py
```

## What It Does

The example demonstrates:

1. **Before Injection**: Calls a dummy function that prints normally
2. **Injection**: Injects a hook at a specific line using `inject_hook()`
3. **After Injection**: Calls the function again - now the hook is executed
4. **Ejection**: Removes the hook using `eject_hook()`
5. **After Ejection**: Calls the function again - back to normal behavior

## Key Concepts

### Function Signature

```python
inject_hook(f: FunctionType, hook: HookType, line: int, arg: Any) -> FunctionType
```

- **f**: The function to instrument
- **hook**: A callable that takes one argument
- **line**: The line number where the hook should be injected
- **arg**: An identifier passed to the hook (used for later removal)

### How It Works

The hook is injected at the bytecode level:
- No source code modification needed
- Hook is called at runtime when execution reaches the specified line
- Can be added and removed dynamically

### Important Notes

1. **Line Numbers**: Must specify the exact line number in the source file
2. **Identifiers**: The `arg` parameter identifies the hook for later removal
3. **Hook Signature**: Hooks must accept one argument (the identifier)
4. **Invalid Lines**: Cannot inject on blank lines, comments, or docstrings

## Example Output

```
1. Calling dummy_function BEFORE injection:
  [DUMMY] Executing dummy_function with x=5, y=3
  [DUMMY] Result is 8

2. Injecting hook at line 19...
  Hook successfully injected!

3. Calling dummy_function AFTER injection:
  [DUMMY] Executing dummy_function with x=10, y=7
  [HOOK INJECTED] Hook called with arg: my_identifier  ← Hook executed!
  [DUMMY] Result is 17

4. BONUS: Ejecting the hook...
  Hook ejected!

5. Calling dummy_function AFTER ejection:
  [DUMMY] Executing dummy_function with x=15, y=5
  [DUMMY] Result is 20  ← Back to normal!
```

## Use Cases

This technique is used in dd-trace-py for:
- **SCA (Software Composition Analysis)**: Auto-instrumenting package imports
- **Dynamic instrumentation**: Adding monitoring without code changes
- **Remote configuration**: Injecting/ejecting hooks based on remote settings

## Related Files

- `ddtrace/internal/bytecode_injection/__init__.py` - Main implementation
- `ddtrace/appsec/sca/_instrumenter.py` - SCA instrumenter using this technique
- `example_bytecode_injection.py` - This example
