---
name: debug-build-times
description: >
  Diagnose and fix slow base venv build times caused by unnecessary recompilation of
  native extensions (CMake, Cython, Rust) across test environment builds. Use when CI base
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

# Diagnose native build caching

Read [Debugging Build Performance](../../../docs/build_system.rst) for cache architecture,
timing variables, and source locations. Use an isolated checkout for cold-build experiments;
clearing compiled artifacts in the user's working tree can disrupt unrelated work.

1. Select a relevant environment with `scripts/run-tests --list <changed-files>`.
2. Set `_DD_DEBUG_EXT=1` and `_DD_DEBUG_EXT_FILE=<output-path>` in the build process's environment
   to capture timings when rebuilding with `scripts/run-tests -r --venv <hash>`.
   The default container runner does not forward these variables from the host. For example,
   temporarily set them in `testrunner.environment` in `docker-compose.base.yml` in the isolated
   checkout, using an output path under `/home/bits/project` so it is visible on the host.
   Remove those diagnostic settings after measuring; they are not part of the fix.
3. Compare cold/warm runs with the same Python, platform, compiler settings, and source revision.
   Distinguish environment reuse from an editable rebuild that actually exercises extension caching.
4. Inspect `scripts/ext_cache.py` restore/save behavior and `setup.py` skip conditions. The Python
   used for cache hashing must match the build interpreter because extension suffixes differ.
   To test `ext_cache` itself, save a completed build, clear only the generated outputs under
   investigation in the isolated checkout, restore the cache, then rebuild with matching settings.
   `run-tests` does not perform that cache save/restore cycle; a warm in-tree rebuild alone
   does not verify cache restoration.
5. Check changed hashes, missing cached outputs, newer inputs, and compile commands before deciding
   that an extension rebuilt unnecessarily. Timing alone is not proof of a cache miss.

Report measured phase times and the evidence for invalidation. Avoid universal wall-clock or
per-extension thresholds: hardware, dependency downloads, and build configuration affect timing.

## Cache helper and round trip

Run these commands from the isolated checkout's repository root, inside `scripts/ddtest` for
container builds. Set `build_python` to the interpreter used by the selected build, not the host
Python. `ext_cache.py` invokes `setup.py` with its own interpreter; it needs the build dependencies
even when the build used an isolated environment. This Linux bootstrap follows
`.gitlab/scripts/build-wheel-helpers.sh`; align dependency versions and build flags with the run
being investigated (requirements are in `pyproject.toml`).

```bash
build_python=/path/to/selected/build/python
cache_python() {
    uv run --no-project --python "$build_python" \
        --with setuptools --with cython --with 'setuptools-rust<2' \
        --with 'cmake>=3.24.2,<3.28' --with 'patchelf>=0.17.0.0' python "$@"
}
cache_hashes() {
    cache_python - "$@" <<'PY'
import sys
from scripts import ext_cache

ext_cache.INPLACE = "--build-lib" not in sys.argv[1:]
extensions, dependencies = ext_cache.invoke_ext_hashes()
for entry in sorted(extensions):
    print("#EXTHASH:", entry)
for name, digest, path in sorted(dependencies):
    print("#SHAREDEPINFO:", (name, digest, str(path)))
PY
}
mkdir -p .cache/ext-cache-probe
cache_hashes
cache_python scripts/ext_cache.py --root .cache/ext-cache-probe save
```

Save only after a successful build. Inspect the reported targets and saved files, then remove
only the generated outputs being tested in this isolated checkout. Do not delete source inputs
or the cache. Restore from the same helper shell:

```bash
cache_python scripts/ext_cache.py --root .cache/ext-cache-probe restore
```

Then rerun the original `scripts/run-tests -r --venv <hash>` from the host for container builds.
Check the restore messages, resulting artifacts, skip/compile messages, and phase timings.
For wheel outputs, call `cache_hashes --build-lib` and add `--build-lib` before `save`/`restore`.
Use `--no-shared-deps` only when intentionally excluding shared C++ dependency trees from the
experiment. Reusing `invoke_ext_hashes()` clears generated `.eggs` before each setup invocation,
avoiding repeated-call failures with older setuptools. Saving prunes old cache generations;
the isolated checkout and probe cache keep that work scoped.

## Hash and timestamp diagnostics

With unchanged sources and settings, compare repeated hashes using the same helper. Preserve
returned records and stderr, but compare just hash records. The helper captures setup stdout
internally; collect rebuild logs separately for skip/compile evidence.

```bash
(
    set -e -o pipefail
    for cache_pass in 1 2; do
        cache_hashes > ".cache/ext-cache-probe/hash-$cache_pass.log" 2>&1
        awk '/^#(EXTHASH|SHAREDEPINFO):/' ".cache/ext-cache-probe/hash-$cache_pass.log" |
            LC_ALL=C sort > ".cache/ext-cache-probe/hash-$cache_pass.txt"
        test -s ".cache/ext-cache-probe/hash-$cache_pass.txt"
    done
    diff -u .cache/ext-cache-probe/hash-1.txt .cache/ext-cache-probe/hash-2.txt
)
```

If records change, inspect `ExtensionHashes.run` and `CMakeExtension.get_sources` in `setup.py`
for generated files entering the source set or changing configuration. A stable hash with no
cached output may mean the artifact was never saved or was pruned; it does not prove a hash bug.

For an unexpected rebuild, replace the example paths below with one candidate output and its
actual input files from the relevant `setup.py` skip check. Non-CMake checks substitute a `.pyx`
sibling for each source when present and include every `ddtrace/**/*.pxd`. CMake checks use
`get_sources()` (including extra source directories) plus dependency artifacts. Check both
in-place and build-directory output candidates; missing CMake dependencies can force a build.

```bash
cache_python - path/to/output.so path/to/input.pyx path/to/declaration.pxd <<'PY'
from pathlib import Path
import sys

output = Path(sys.argv[1])
if not output.is_file():
    raise SystemExit(f"Missing output: {output}")
for source in sorted({Path(name) for name in sys.argv[2:]}):
    if not source.is_file():
        print(f"Missing input: {source}")
    elif source.stat().st_mtime > output.stat().st_mtime:
        print(f"Newer input: {source}")
PY
```

No reported offenders only rules out those inputs for that output. Also inspect forced builds,
`DD_CMAKE_INCREMENTAL_BUILD`, and the actual compile command before concluding caching failed.
