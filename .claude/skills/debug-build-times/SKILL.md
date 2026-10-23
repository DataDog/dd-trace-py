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
