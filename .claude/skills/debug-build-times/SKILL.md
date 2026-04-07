---
name: debug-build-times
description: >
  Diagnose and fix slow base venv build times caused by unnecessary recompilation of
  native extensions (CMake, Cython, Rust) across riot generate runs. Use when CI base
  venv builds are slow, when ext_cache isn't saving time, or when investigating warm
  build regressions.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Edit
  - Write
  - TodoWrite
---

# Debug Build Times Skill

## Overview

The build system compiles many native extensions (CMake C++, Cython, Rust). In CI,
`ext_cache.py` caches compiled `.so` files between runs. On a warm run (extensions
already in cache), the entire `pip install -e .` should complete in **under 30s**.

The debug loop uses `test_venv_cache.sh` (repo root) to simulate cold → warm cycles
locally.

## Quick Start

There is no permanent test script — create a throwaway `test_venv_cache.sh` in the
repo root (don't commit it) with the contents below, then run it:

```bash
bash test_venv_cache.sh 3.13   # or whichever Python version
cat debug_ext_metadata_cold.txt
cat debug_ext_metadata_warm.txt
```

### test_venv_cache.sh (recreate when needed)

```bash
#!/usr/bin/env bash
set -euo pipefail

PYTHON_VERSION="${1:-3.12}"
CACHE_ROOT="/tmp/dd_ext_cache_test"
EXT_CACHE_VENV="/tmp/dd_ext_cache_venv"
METADATA_COLD="debug_ext_metadata_cold.txt"
METADATA_WARM="debug_ext_metadata_warm.txt"

header() { echo; echo "══════════════════════════════════════════"; echo "  $*"; echo "══════════════════════════════════════════"; }
step()   { echo "── $*"; }

# One-time venv for running ext_cache.py (mirrors CI's EXT_CACHE_VENV)
if [ ! -d "$EXT_CACHE_VENV" ]; then
    step "Creating ext_cache venv"
    "python${PYTHON_VERSION}" -m venv "$EXT_CACHE_VENV"
    "$EXT_CACHE_VENV/bin/pip" install -q cmake setuptools_rust Cython
fi

cache_restore() { "$EXT_CACHE_VENV/bin/python" scripts/ext_cache.py --root "$CACHE_ROOT" restore; }
cache_save()    { "$EXT_CACHE_VENV/bin/python" scripts/ext_cache.py --root "$CACHE_ROOT" save; }

run_build() {
    local label="$1" metadata_out="$2"
    header "$label build (python $PYTHON_VERSION)"
    step "Restoring from cache"; cache_restore
    step "riot generate"
    export _DD_DEBUG_EXT=1 _DD_DEBUG_EXT_FILE="$metadata_out"
    riot -P generate --python="$PYTHON_VERSION"
    unset _DD_DEBUG_EXT _DD_DEBUG_EXT_FILE
    step "Saving to cache"; cache_save
    cat "$metadata_out" 2>/dev/null || echo "(no metadata written)"
}

header "Clearing test state"
rm -rf ".riot/venv_py${PYTHON_VERSION/./}"*
rm -rf "$CACHE_ROOT"
# Remove compiled .so files for a true cold state
find ddtrace -name "*.so" -o -name "*.dylib" -o -name "*.pyd" | grep -v "_vendor" | xargs rm -f 2>/dev/null || true

run_build "COLD" "$METADATA_COLD"
header "Clearing riot venv for warm run"
rm -rf ".riot/venv_py${PYTHON_VERSION/./}"*
run_build "WARM" "$METADATA_WARM"

header "Summary"
diff --unified=0 --label COLD --label WARM "$METADATA_COLD" "$METADATA_WARM" || true
```

Key points:
- Uses `/tmp/dd_ext_cache_test` as cache root so `.ext_cache` is untouched
- Creates a separate `/tmp/dd_ext_cache_venv` to run `scripts/ext_cache.py` (needs cmake/setuptools_rust/Cython)
- `_DD_DEBUG_EXT=1` activates `DebugMetadata` timing in `setup.py`
- Cold run wipes riot venv + cache + source-tree `.so` files; warm run only wipes the riot venv
- The diff at the end highlights any timing discrepancies between cold and warm

**Expected results:** Cold ~100s, warm <10s. Any extension showing >0.05s on warm is rebuilding unnecessarily.

Output files are written via `_DD_DEBUG_EXT_FILE`. Enable debug metadata by setting
`_DD_DEBUG_EXT=1` in the environment (or temporarily flip `DebugMetadata.enabled = True`
in `setup.py` while investigating — revert when done).

## Architecture: How the Build Works

```
riot generate
  └─ pip install -e .
       ├─ build_py  → LibraryDownloader.run()
       │    ├─ CleanLibraries.remove_artifacts()  ← SKIPPED when INCREMENTAL=1
       │    └─ LibDDWafDownload.run()
       └─ build_ext → CustomBuildExt.run()
            ├─ build_rust()          → Rust _native extension
            ├─ build_libdd_wrapper() → libdd_wrapper.so (C++)
            ├─ build_shared_deps()   → absl (once, cached by sentinel file)
            └─ super().run()         → build_extension() for every ext
                 ├─ CMakeExtension  → build_extension_cmake()
                 │    └─ skip if .so newer than sources (INCREMENTAL check)
                 └─ Cython/C ext   → skip if .so newer than .pyx sources
```

**ext_cache flow (in CI / test script):**

```
ext_cache.py restore  →  copies .so files into source tree
pip install -e .      →  skip checks fire, nothing recompiles
ext_cache.py save     →  copies .so files into cache
```

## Known Root Causes of Warm Rebuilds

### 1. `CleanLibraries.remove_artifacts()` deletes restored `.so` files
**Where:** `setup.py` → `LibraryDownloader.run()` → `CleanLibraries.remove_artifacts()`  
**Symptom:** All extensions rebuild on every run despite cache restore.  
**Fix:** Guard the call with `if not CustomBuildExt.INCREMENTAL:` (already done).

### 2. CMakeExtension skip check gated on `IS_EDITABLE`
**Where:** `setup.py` → `build_extension_cmake()` — the `if IS_EDITABLE and self.INCREMENTAL` guard.  
**Symptom:** CMake extensions always rebuild; IS_EDITABLE is never True during riot's `pip install -e .`.  
**Fix:** Remove the `IS_EDITABLE` guard — check only `self.INCREMENTAL` (already done).

### 3. Cython-generated `.c` files poison CMakeExtension hashes
**Where:** `setup.py` → `CMakeExtension.get_sources()` → `is_valid_source()`.  
**Symptom:** `_memalloc` (or other CMake exts sharing a directory with Cython exts)
has a non-deterministic hash; `needs_rebuild=True` even when `.so` is present.  
**Root cause:** `cythonize()` writes `.c` files with the current timestamp during
the same `pip install` run. Those `.c` files appear newer than the just-restored `.so`.  
**Fix:** Exclude `.c` (and `.so`, `.dylib`, `.dll`, `.pyd`) from `is_valid_source()` (already done).

### 4. No skip check for Cython/C extensions
**Where:** `setup.py` → `build_extension()` else branch.  
**Symptom:** All Cython extensions rebuild (2-4s each) even when `.so` is restored.  
**Fix:** Added `newer_group` check using `.pyx` file mtimes (not `.c` files, which get
touched by `cythonize()`). Also includes all `.pxd` files so declaration changes
invalidate the cache (already done).

### 5. Double extension processing (historical)
**Where:** `CustomBuildExt.run()` called `super().run()` then looped explicitly again.  
**Symptom:** Timing data showed extensions rebuilding twice; second pass (fast, CMake
incremental) overwrote first pass (slow) in `DebugMetadata.build_times`.  
**Fix:** Removed the explicit loop — `super().run()` is sufficient (already done).

## Diagnosing a New Warm Rebuild

### Step 1: Enable phase timing
Set `_DD_DEBUG_EXT=1` (or temporarily set `DebugMetadata.enabled = True` in setup.py)
and run `bash test_venv_cache.sh 3.13`. The metadata files show per-phase and
per-extension times.

### Step 2: Identify the slow extension
Look at `debug_ext_metadata_warm.txt`. Any extension with >0.05s on warm is rebuilding.

### Step 3: Check the skip condition
Add a debug print to `build_extension_cmake` or the Cython skip block:
```python
print(f"DEBUG {ext.name}: ext_path={ext_path} exists={ext_path.exists()} needs_rebuild={needs_rebuild}")
```
Then run:
```bash
.riot/venv_py3131/bin/pip --disable-pip-version-check install -e . -v 2>&1 | grep "DEBUG\|skipping\|building"
```
(Use `pip install -v` — the `-v` flag makes pip show the build output.)

### Step 4: Find the newer source
If `needs_rebuild=True` but the `.so` exists, something in `get_sources()` is newer.
```python
# In a Python shell after restoring from cache:
from pathlib import Path
from distutils.dep_util import newer_group
import sysconfig
suffix = sysconfig.get_config_var('EXT_SUFFIX')
source_dir = Path('ddtrace/profiling/collector')  # adjust as needed
ext_path = source_dir / f'_memalloc{suffix}'
srcs = [s for s in source_dir.glob('**/*') if s.is_file() and s.suffix not in {'.py','.pyc','.pyi','.c','.so','.dylib','.dll','.pyd'}]
ext_mtime = ext_path.stat().st_mtime
newer = [(str(s), round(s.stat().st_mtime - ext_mtime, 3)) for s in srcs if s.stat().st_mtime > ext_mtime]
print(newer)
```

### Step 5: Check ext_cache is actually restoring
```bash
/tmp/dd_ext_cache_venv/bin/python scripts/ext_cache.py --root /tmp/dd_ext_cache_test restore 2>&1 | grep -i "warning\|error\|restoring"
```
Warnings about "No cached files found" mean the hash changed between save and restore.
Run `python setup.py ext_hashes --inplace` twice and compare — if hashes differ, there's
a hash stability bug.

## Key Files

| File | Purpose |
|------|---------|
| `setup.py` | `CustomBuildExt`, `CMakeExtension.get_sources()`, `DebugMetadata`, `LibraryDownloader` |
| `scripts/ext_cache.py` | Cache/restore `.so` files and shared dep install trees |
| `cmake/abseil/CMakeLists.txt` | Standalone abseil build (shared between extensions) |
| `test_venv_cache.sh` | Local cold→warm test script |

## Shared Dependency (abseil) Cache

Abseil is built once via `CustomBuildExt.build_shared_deps()` and installed to
`.download_cache/_cmake_deps/absl_install`. The `SharedDep.is_built()` sentinel
(`.dep_build_info` file containing a config hash) prevents rebuilding on every run.

The `scripts/ext_cache.py` caches the entire install tree under
`.ext_cache/shared_deps/absl/<config_hash>/`. The config hash is keyed on:
version + compile mode + platform + machine arch + ARCHFLAGS.

Abseil showing `0.00s` in cold runs is expected if `.download_cache` was not cleared
(it persists between test runs). To test a true abseil cold build:
```bash
rm -rf .download_cache/_cmake_deps/absl_install
```

## Environment Variables

| Variable | Effect |
|----------|--------|
| `DD_CMAKE_INCREMENTAL_BUILD` | `1` (default) enables skip checks; `0` forces full rebuild |
| `_DD_DEBUG_EXT` | Any value enables `DebugMetadata` timing output |
| `_DD_DEBUG_EXT_FILE` | Override output filename (default: `debug_ext_metadata.txt`) |
| `DD_SETUP_FORCE_CYTHONIZE` | `1` forces Cython to regenerate all `.c` files |
| `DD_COMPILE_ABSEIL` | `0`/`false` disables abseil compilation (uses fallback) |
