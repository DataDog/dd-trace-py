---
name: find-cpython-usage
description: >
  Find all CPython internal headers and structs used in the codebase, particularly
  for profiling functionality. Use this when adding support for a new Python version
  to identify what CPython internals we depend on.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TodoWrite
---

# Find CPython Internal Usage Skill

This skill helps identify all CPython internal headers and structures used in the
codebase, which is essential when adding support for new Python versions.

## When to Use This Skill

Use this skill when:
- Adding support for a new Python version
- Investigating CPython API dependencies
- Understanding what internal APIs the profiler uses
- Preparing to compare CPython versions

## Key Principles

1. **Focus on internal headers** - These are most likely to change between versions
2. **Check all native extensions** - CPython internals are used in profiling, AppSec, and internal modules
3. **Look for struct field access** - Direct field access is version-sensitive
4. **Document findings** - Keep track of what you find for comparison

## How This Skill Works

### Step 1: Find CPython Header Includes

Search for CPython header includes across all C/C++/Cython files:

```bash
# Find all CPython internal header includes
grep -r "include.*internal/pycore" ddtrace/ --include="*.c" --include="*.cpp" --include="*.h" --include="*.hpp" --include="*.pyx"

# Find all CPython cpython header includes
grep -r "include.*cpython" ddtrace/ --include="*.c" --include="*.cpp" --include="*.h" --include="*.hpp" --include="*.pyx"

# Find all Python.h includes (indicates CPython API usage)
grep -r "#include.*Python\.h" ddtrace/ --include="*.c" --include="*.cpp" --include="*.h" --include="*.hpp" --include="*.pyx"

# Find frameobject.h includes (common CPython API)
grep -r "#include.*frameobject\.h" ddtrace/ --include="*.c" --include="*.cpp" --include="*.h" --include="*.hpp" --include="*.pyx"

# Find PyO3 FFI usage in Rust (Rust extensions may use CPython internals)
grep -r "pyo3_ffi::\|pyo3::ffi::" src/native/ --include="*.rs" || true
```

**Note:** These patterns search across all native extension files (`.c`, `.cpp`, `.h`, `.hpp`, `.pyx`, `.rs`)
regardless of their location in the codebase. Check `setup.py` to see which extensions are built.
Rust extensions use PyO3 which may access CPython internals through the `pyo3_ffi` module.

### Step 2: Find Struct Field Access

Search for direct struct field access and struct definitions across all native files:

```bash
# Find struct field accesses (arrow operator)
grep -r "->f_\|->[a-z_]*\." ddtrace/ --include="*.c" --include="*.cpp" --include="*.h" --include="*.hpp"

# Find struct definitions
grep -r "struct.*Py" ddtrace/ --include="*.c" --include="*.cpp" --include="*.h" --include="*.hpp"

# Find common CPython struct usage
grep -r "PyFrameObject\|PyThreadState\|_PyInterpreterFrame" ddtrace/ --include="*.c" --include="*.cpp" --include="*.h" --include="*.hpp" --include="*.pyx"
grep -r "PyFrameObject\|PyThreadState\|PyInterpreterState" src/native/ --include="*.rs" || true

# Find PyCodeObject usage
grep -r "PyCodeObject" ddtrace/ --include="*.c" --include="*.cpp" --include="*.h" --include="*.hpp" --include="*.pyx"
grep -r "PyCodeObject" src/native/ --include="*.rs" || true
```

**Look for patterns like:**
- Frame structure access (`PyFrameObject`, `_PyInterpreterFrame`)
- Thread state access (`PyThreadState`)
- Code object access (`PyCodeObject`)
- Generator/coroutine structures
- Asyncio task structures

### Step 3: Identify Common Structures

Common CPython structures we typically access:

**Frame structures:**
- `PyFrameObject` / `struct _frame`
- `_PyInterpreterFrame`

**State structures:**
- `PyThreadState`
- `PyInterpreterState`
- `_PyRuntimeState`

**Code structures:**
- `PyCodeObject`

**Generator structures:**
- `PyGenObject`
- `PyAsyncGenASend`

**Asyncio structures:**
- `FutureObj`
- `TaskObj`

### Step 4: Document Findings

Create a list of:
- All headers that are included
- All structs that are accessed
- All struct fields that are used directly

This will be used in the next step to compare against the new Python version.

## Native Extensions Using CPython APIs

To find all native extensions that may use CPython APIs, check `setup.py`:

```bash
# View all native extensions defined in setup.py
grep -A 5 "Extension\|CMakeExtension\|Cython.Distutils.Extension\|RustExtension" setup.py
```

The `setup.py` file defines all native extensions (C, C++, CMake, Cython, and Rust) that
are built for the project. Not all extensions use CPython internals - focus on those
that access frame objects, thread state, or internal structures when searching for
CPython API usage.

**Note:** Rust extensions use PyO3 bindings which may access CPython internals through
`pyo3_ffi` module. Search Rust source files (`.rs`) for CPython API usage as well.

## Common Headers to Look For

The grep commands above will identify which CPython headers are actually used in the codebase.
Common patterns include:

**Public Headers:**
- Headers matching `*.h` in `Include/` directory (e.g., `frameobject.h`, `unicodeobject.h`)
- Headers in `Include/cpython/` directory (e.g., `cpython/genobject.h`)

**Internal Headers (require `Py_BUILD_CORE`):**
- Headers matching `internal/pycore*.h` pattern
- These are most likely to change between Python versions

Focus on headers that are actually found by the grep commands rather than maintaining
a hardcoded list, as the headers used may change over time.

## Output Format

After running this skill, you should have:
1. A list of all CPython headers included in the codebase
2. A list of all CPython structs accessed
3. A list of struct fields accessed directly
4. Files that use each header/struct

This information can then be used with the `compare-cpython-versions` skill to identify what changed.

## Related

- **compare-cpython-versions skill**: Use findings from this skill to compare versions
