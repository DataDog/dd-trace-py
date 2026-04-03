---
name: compare-cpython-versions
description: >
  Compare CPython source code between two Python versions to identify changes in
  headers and structs. Use this when adding support for a new Python version to
  understand what changed between versions.
allowed-tools:
  - Bash
  - Read
  - Grep
  - WebSearch
  - TodoWrite
---

# Compare CPython Versions Skill

This skill helps compare CPython source code between two Python versions to identify
changes in headers, structs, and APIs that affect our codebase.

## When to Use This Skill

Use this skill when:
- Adding support for a new Python version
- Need to understand what changed between versions
- Investigating compatibility issues
- Have a list of headers/structs from `find-cpython-usage` skill

## Key Principles

1. **Compare systematically** - Focus on headers and structs identified in Step 1
2. **Use multiple methods** - Git diff, manual diff, or AI-assisted comparison
3. **Document changes** - Note all breaking changes and API modifications
4. **Check context** - Understand why changes were made (PEPs, GitHub issues)

## How This Skill Works

### Step 1: Prepare CPython Repository

```bash
# Create ~/dd directory if it doesn't exist
mkdir -p ~/dd

# Clone CPython repository if needed (to ~/dd/cpython)
if [ ! -d ~/dd/cpython ]; then
    git clone https://github.com/python/cpython.git ~/dd/cpython
    cd ~/dd/cpython
    git fetch --tags
else
    cd ~/dd/cpython
    # Update existing repository
    git fetch --tags
    git fetch origin
fi
```

### Step 2: Compare Specific Headers

Using the list of headers from `find-cpython-usage`, compare each header between
the old version and new version. Replace `OLD_VERSION` and `NEW_VERSION` with the
actual version tags (e.g., `v3.13.0`, `v3.14.0`):

```bash
# Compare specific headers between versions
git diff OLD_VERSION NEW_VERSION -- Include/internal/pycore_frame.h
git diff OLD_VERSION NEW_VERSION -- Include/frameobject.h

# Compare all internal headers
git diff OLD_VERSION NEW_VERSION -- 'Include/internal/pycore*.h'

# Compare specific struct definitions
git diff OLD_VERSION NEW_VERSION -- Include/internal/pycore_frame.h | grep -A 20 "struct _PyInterpreterFrame"
```


### Step 3: Identify Changes

For each header/struct, look for:

**Struct Changes:**
- Field additions
- Field removals
- Field type changes
- Field reordering
- Struct moves to different headers

**API Changes:**
- Removed functions/structures
- New functions/structures
- Changed function signatures
- Deprecated APIs

**Header Changes:**
- Headers moved to different locations
- Headers split or merged
- New headers introduced

**Enum Value Changes (easy to miss, high impact):**

Enum renumbering is particularly dangerous because the code compiles cleanly but silently
misclassifies values at runtime. Always explicitly check every enum echion uses:

- `PyFrameState` (in `pycore_frame.h`) — renumbered completely between 3.14 and 3.15:
  `FRAME_EXECUTING` changed from `0` to `4`, `FRAME_CREATED` from `-3` to `0`. Code
  that compared against the old constants compiled fine but returned wrong results.
- `_frameowner` (in `pycore_interpframe_structs.h`) — `FRAME_OWNED_BY_CSTACK` was
  removed in 3.15.

For every enum echion touches, run:

```bash
git diff OLD_VERSION NEW_VERSION -- Include/internal/pycore_frame.h | grep -A 30 "enum "
git diff OLD_VERSION NEW_VERSION -- Include/internal/pycore_interpframe_structs.h | grep -A 30 "enum "
```

**Lock in compile-time contracts with `static_assert`:**

After confirming enum values, add or update `static_assert` checks in
`ddtrace/internal/datadog/profiling/stack/test/test_cpython_layout_contracts.cpp`.
These fire at *build time* against the actual CPython headers — if CPython renumbers
an enum value, the build breaks immediately before any test runner runs. Example:

```cpp
// Add a versioned block for the new CPython version:
#if PY_VERSION_HEX >= 0x030f0000
static_assert(FRAME_CREATED == 0,
              "AIDEV-NOTE: PyFrameState::FRAME_CREATED changed — update tasks.h and tasks.cc");
static_assert(FRAME_EXECUTING == 4,
              "AIDEV-NOTE: PyFrameState::FRAME_EXECUTING changed — update tasks.cc gen_is_running check");
// ... one assert per enum value echion depends on
#endif
```

Also add a `-Wswitch`-enabled exhaustive `switch` (no `default:` case) for any enum
used in a dispatch — the compiler will warn if CPython adds a new value.

### Step 4: Analyze Impact

For each change identified:

1. **Understand the change:**
   - Why was it changed? (Check Python's What's New, PEPs, or GitHub issues)
   - Is it a breaking change or backward compatible?
   - What's the replacement API?
   - **Find the specific commit(s) that introduced the change:**

     ```bash
     # Find commits that modified a specific file between versions
     git log OLD_VERSION..NEW_VERSION -- Include/internal/pycore_frame.h

     # Find commits that mention a specific struct or function
     git log OLD_VERSION..NEW_VERSION --all --grep="_PyInterpreterFrame" -- Include/

     # Show the commit that introduced a specific change
     git log -p OLD_VERSION..NEW_VERSION -S "struct _PyInterpreterFrame" -- Include/
     ```

   - **Find related GitHub issues:**
     - Check commit messages for issue references (e.g., `gh-123923`, `#123923`)
     - Search CPython GitHub issues: `https://github.com/python/cpython/issues`
     - Look for "What's New" documentation: `https://docs.python.org/3/whatsnew/`
     - Check PEPs if the change is part of a larger feature

2. **Assess impact:**
   - Which files in our codebase are affected?
   - What functionality might break?
   - Are there alternative approaches?

3. **Document findings:**
   - Create a summary document of key changes
   - Note any breaking changes
   - List files that need updates

### Step 5: Use AI Tools (Optional)

You can use AI coding assistants to help analyze differences by:
- Providing header file contents from both versions
- Asking about specific struct changes
- Understanding migration paths

## Common Change Patterns

When comparing versions, look for these types of changes (examples):

**Struct Field Changes:**
- Field type changes (e.g., pointer types → tagged pointer types)
- Field renamed
- Field removed and replaced with different mechanism
- Field reordering

**Header Moves:**
- Internal headers moved to new locations
- Structs moved between headers
- Headers split or merged

**API Deprecations:**
- Internal functions removed
- Public API replacements available
- Function signature changes

## Output Format

After running this skill, you should have:
1. A list of all changed headers
2. A list of all changed structs with details
3. Impact assessment for each change
4. Files in our codebase that need updates

## Related

- **find-cpython-usage skill**: Use to identify what to compare
