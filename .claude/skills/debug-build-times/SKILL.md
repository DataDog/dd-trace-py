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

For architecture, root causes, key files, and env vars see
**[docs/build_system.rst](docs/build_system.rst)** — "Debugging Build Performance" section.

## Quick Start

On a warm run (extensions cached) `pip install -e .` should complete in **under 30s**.

Create a throwaway `test_venv_cache.sh` in the repo root (don't commit it), run it,
then inspect the metadata files:

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
# IMPORTANT: use a version-specific venv to match CI's ext_cache_venv${PYTHON_VERSION} pattern.
# ext_cache.py uses sys.executable to call setup.py ext_hashes, which computes extension
# targets with the running Python's suffix (e.g. cpython-313).  If the venv Python doesn't
# match the build Python, cache save/restore silently operates on the wrong suffix and the
# ext_cache becomes a no-op for that Python version.
EXT_CACHE_VENV="/tmp/dd_ext_cache_venv${PYTHON_VERSION}"
METADATA_COLD="debug_ext_metadata_cold.txt"
METADATA_WARM="debug_ext_metadata_warm.txt"

header() { echo; echo "══════════════════════════════════════════"; echo "  $*"; echo "══════════════════════════════════════════"; }
step()   { echo "── $*"; }

# One-time venv for running ext_cache.py (mirrors CI's EXT_CACHE_VENV)
if [ ! -d "$EXT_CACHE_VENV" ]; then
    step "Creating ext_cache venv (python${PYTHON_VERSION})"
    "python${PYTHON_VERSION}" -m venv "$EXT_CACHE_VENV"
    "$EXT_CACHE_VENV/bin/pip" install -q wheel cmake setuptools_rust Cython
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
rm -rf .eggs  # stale eggs cause ENOTEMPTY on Python ≤3.10's legacy setuptools
# Remove compiled .so files for a true cold state
find ddtrace -name "*.so" -o -name "*.dylib" -o -name "*.pyd" | grep -v "_vendor" | xargs rm -f 2>/dev/null || true

run_build "COLD" "$METADATA_COLD"
header "Clearing riot venv for warm run"
rm -rf ".riot/venv_py${PYTHON_VERSION/./}"*
run_build "WARM" "$METADATA_WARM"

header "Summary"
diff --unified=0 --label COLD --label WARM "$METADATA_COLD" "$METADATA_WARM" || true
```

**Expected results:** Cold ~100s, warm <10s. Any extension showing >0.05s on warm is
rebuilding unnecessarily.

## Diagnosing a New Warm Rebuild

### Step 1: Enable phase timing

Run `bash test_venv_cache.sh 3.13` with `_DD_DEBUG_EXT=1` (the script sets this). The
metadata files show per-phase and per-extension times.

### Step 2: Identify the slow extension

In `debug_ext_metadata_warm.txt`, any extension with >0.05s is rebuilding.

### Step 3: Check the skip condition

```python
print(f"DEBUG {ext.name}: ext_path={ext_path} exists={ext_path.exists()} needs_rebuild={needs_rebuild}")
```

```bash
.riot/venv_py3131/bin/pip --disable-pip-version-check install -e . -v 2>&1 | grep "DEBUG\|skipping\|building"
```

### Step 4: Find the newer source

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
/tmp/dd_ext_cache_venv3.13/bin/python scripts/ext_cache.py --root /tmp/dd_ext_cache_test restore 2>&1 | grep -i "warning\|error\|restoring"
```

Warnings about "No cached files found" mean the hash changed between save and restore.
Run `python setup.py ext_hashes --inplace` twice and compare — if hashes differ, there's
a hash stability bug.
