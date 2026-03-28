.. _profiling_new_cpython:

Profiling and new CPython versions
==================================

This guide is for maintainers who add support for a **new CPython minor release** (e.g. 3.15) across
**everything dd-trace-py owns in the Continuous Profiler product**: **stack** (CPU / wall samples,
Echion), **asyncio** integration for stack and tasks, **lock** profilers (threading + asyncio),
**memory** and **heap** (memalloc), **exception** profiling, **PyTorch** hook, **ddup** export, build
gates, Riot/CI, and **validation tests** for each area.

The best reference implementation is `PR #15546`__ (feat(profiling): support Python 3.14): Echion
frame/task/asyncio changes, ``setup.py`` un-gating, profiling defaults, Riot venv splits, tests, and
a release note.

__ https://github.com/DataDog/dd-trace-py/pull/15546

Preparation: what tends to break
--------------------------------

When CPython bumps, expect changes in:

* ``_PyInterpreterFrame`` and related **internal headers** (include paths move between releases;
  fields may become ``_PyStackRef``, ``stackpointer`` vs ``stacktop``, ``localsplus`` layout).
* **Tagged pointers** on frame/code objects (recover ``PyObject*`` per upstream notes, e.g.
  ``python/cpython#123923`` for 3.14).
* **Asyncio** C layout: ``FutureObj`` / ``TaskObj`` struct layout and where **native** tasks live
  (e.g. per-thread / per-interpreter linked lists and ``asyncio_tasks_head`` in 3.14+).
* **Python-visible** asyncio: policy class renames, whether ``_scheduled_tasks`` / ``_eager_tasks``
  are exported from the C module or live only in Python.

Read the PR #15546 description for the concrete 3.14 deltas before extrapolating to the next
version.

Discover CPython deltas (before writing code)
---------------------------------------------

1. Clone **python/cpython** and compare the **previous supported release** to the **target** (e.g.
   ``v3.14.x`` vs ``v3.15.x`` or ``main`` near release).

2. Diff headers we depend on (paths change; always verify):

   * ``Include/internal/pycore_interpframe_structs.h``, ``pycore_frame.h``, adjacent ``pycore_*``
     headers.
   * ``Include/cpython/genobject.h`` and anything **PyGen_\*** / yield-from paths used in Echion.
   * ``Modules/_asynciomodule.c`` and interpreter/thread structs that hold asyncio task lists.

3. In **dd-trace-py**, use the repository skills (see ``AGENTS.md``):

   * ``.claude/skills/find-cpython-usage/SKILL.md`` — enumerate internal includes and structs we
     touch.
   * ``.claude/skills/compare-cpython-versions/SKILL.md`` — systematic compare workflow.

4. **Version hex:** Python 3.15 is gated with ``PY_VERSION_HEX >= 0x030f0000``. When adding a new
   branch, keep older release guards (e.g. ``0x030e0000`` for 3.14) and only split when behavior
   or layout **diverges** from the prior release.

Quick grep in dd-trace-py (find prior-version guards):

.. code-block:: bash

   rg 'PY_VERSION_HEX|0x030e' ddtrace/internal/datadog/profiling ddtrace/profiling setup.py
   rg '3, 14|3\\.14' tests ddtrace setup.py riotfile.py

Native stack profiler (Echion) — layout in this repo
---------------------------------------------------

CMake extension and sources live under:

.. code-block:: text

   ddtrace/internal/datadog/profiling/stack/
   ├── echion/echion/          # headers (frame, tasks, threads, state, greenlets, …)
   │   └── cpython/tasks.h     # FutureObj / TaskObj mirrors
   └── src/echion/             # frame.cc, threads.cc, stack_chunk.cc, …

(Older branches or docs may say ``stack_v2``; on current ``main`` the path is ``stack/``, defined
in ``setup.py`` as ``STACK_DIR`` under ``ddtrace/internal/datadog/profiling/stack``.)

Typical files to revisit (mirror PR #15546):

+---------------------------+------------------------------------------+
| Area                      | Files                                    |
+===========================+==========================================+
| Frame ABI / includes      | ``stack/echion/echion/frame.h``,         |
|                           | ``stack/src/echion/frame.cc``            |
+---------------------------+------------------------------------------+
| Task / Future layouts     | ``stack/echion/echion/cpython/tasks.h``  |
+---------------------------+------------------------------------------+
| Asyncio task enumeration  | ``stack/echion/echion/tasks.h``,         |
|                           | ``stack/echion/echion/threads.h``,       |
|                           | ``stack/src/echion/threads.cc``          |
+---------------------------+------------------------------------------+
| Misc guards               | ``state.h``, ``greenlets.h``,            |
|                           | ``stack_chunk.cc``                       |
+---------------------------+------------------------------------------+

Build against the **target** interpreter first and fix compile errors. Then run automated tests for
**stack** and **asyncio** (see `Validate all profiling features`_).

For C/C++ conventions and safety expectations, see ``.cursor/rules/native-code.mdc`` (if present).

Python-side integration
-------------------------

* ``ddtrace/profiling/_asyncio.py`` — event-loop policy names, weak sets for scheduled/eager tasks,
  version-guarded access patterns.
* Search under ``ddtrace/profiling/`` for ``sys.version_info``, ``PY_MAJOR_VERSION``, and similar.

Build and product gating
-------------------------

* ``setup.py`` — Ensure **memalloc**, **ddup**, and **stack** CMake extensions (and Rust profiling
  features, if gated) are **not** skipped on the new Python version. PR #15546 **removed**
  ``sys.version_info < (3, 14)`` style exclusions; do the same for ``(3, 15)`` when enabling 3.15.
  Add a **new** upper bound only if a **future** version is known broken.

* ``ddtrace/internal/settings/profiling.py`` — Remove any “force stack profiler off on X.Y” guards.
  Keep **ddup** load failures honest: log and disable profiling when the extension truly fails to
  import.

CI, Riot, and dependencies
---------------------------

* ``riotfile.py`` — Add or extend ``Venv(pys="3.15", ...)`` where a new Python needs different pins
  (examples from 3.14 work: **uwsgi**, **protobuf**, **gevent**, memalloc/**lz4** quirks). Follow
  existing patterns for ``select_pys`` and comments explaining version caps.

* Regenerate ``.riot/requirements/*.txt`` when adding venvs (same workflow as other Python bumps).

* Grep tests: ``3.14``, ``3, 14``, ``max_version``, profiling-related ``skip``.

Validate all profiling features (minor-version migration)
---------------------------------------------------------

Before merging support for a new CPython, treat **each profiler surface** as part of the migration:
ABI changes often break **stack** first, but **memalloc**, **locks**, and **exceptions** use native or
C API-adjacent code that must still pass on the new version.

Automated tests (what to run)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use **`scripts/run-tests`** (see :ref:`testing_guidelines` in ``contributing-testing``) — **never**
raw ``pytest`` for full-suite validation. For profiling, CI maps paths to Riot via
**`tests/profiling/suitespec.yml`**: patterns such as **`profile$`**, **`profile-uwsgi`**, and
**`profile-memalloc`**.

**Feature → code → tests** (paths relative to ``ddtrace/profiling/`` or ``tests/profiling/``):

* **Stack / wall / CPU** — ``collector/stack.py`` and Echion under
  ``ddtrace/internal/datadog/profiling/stack/``. Tests: ``collector/test_stack.py``,
  ``collector/test_stack_native.py``, ``test_accuracy.py``, and the many
  ``collector/test_asyncio_*.py`` files for asyncio stack semantics.

* **Locks** — ``collector/threading.py``, ``collector/asyncio.py``, ``collector/_lock.pyx``. Tests:
  ``collector/test_threading.py``, ``collector/test_lock_reflection.py``,
  ``collector/lock_test_common.py``, plus asyncio tests that cover lock collectors.

* **Memory (allocations)** — ``collector/memalloc.py`` and ``collector/_memalloc*``. Tests:
  ``collector/test_memalloc.py``, ``test_memalloc_fork.py``,
  ``collector/test_copy_memory_stats.py``.

* **Heap (live)** — same memalloc pipeline; ``collector/test_heap_tracker_count.py``.

* **Exceptions** — ``collector/exception.py``; ``collector/test_exception.py``.

* **PyTorch** — ``collector/pytorch.py``; ``test_pytorch.py``.

* **Profiler / scheduler** — ``profiler.py``, ``scheduler.py``; ``test_profiler.py``,
  ``test_scheduler.py``, ``test_profiling_config.py``.

* **ddup / export** — internal ddup + ``tests/profiling/exporter/test_ddup.py``.

**Practical matrix:**

* **Stack / Echion / asyncio framing:** run the **profile** suite (``profile$``); include
  ``collector/test_stack_native.py`` and representative ``test_asyncio_*.py`` files while iterating.
* **Memalloc / heap:** run **profile-memalloc**; always include ``collector/test_memalloc.py`` and
  ``collector/test_heap_tracker_count.py``.
* **Locks / threading:** use ``collector/test_threading.py`` and related asyncio lock tests (file is
  large—narrow with ``run-tests`` on touched paths during development, then full profile suite before
  merge).
* **Full profiling regression:** ``scripts/run-tests`` over ``tests/profiling/`` or let the script pick
  venvs from changed files; locally mirror CI with ``riot run …`` **profile$** / **profile-memalloc** /
  **profile-uwsgi** as needed.

**New code paths** (new env flag, CPython branch, or collector behavior) should get **unit or
subprocess tests** next to the nearest file above; follow existing patterns (many tests use
``@pytest.mark.subprocess`` and init helpers in ``tests/profiling/collector/conftest.py``).

Manual / dogfood checks (optional but recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Automation does not replace **real workloads** or **Profiling Explorer** behavior. On staging or a
one-off service, with **Python version + ddtrace commit + ``DD_PROFILING_*``** documented:

* **Stack / CPU:** visible stacks and CPU/wall samples; timeline if enabled.
* **Locks:** lock / lock-wait views; exercise ``threading`` and ``asyncio`` primitives; if using
  **``DD_PROFILING_LOCK_EXCLUDE_MODULES``**, compare with it unset vs set.
* **Memory / heap:** allocation and live-heap signal under load.
* **Exceptions:** exception profiling after controlled errors.
* **PyTorch:** small torch workload when that collector is enabled.
* **Export:** optional **``DD_PROFILING_OUTPUT_PPROF``** for local pprof inspection.

Release notes
~~~~~~~~~~~~~

* Add a **release note** with the **releasenote** skill (``AGENTS.md``).
* Smoke / telemetry / serverless: grep for version conditionals if profiling availability changed
  (see files touched in PR #15546).

Suggested order of work
-----------------------

#. CPython header/asyncio diff + in-repo grep for the previous release’s ``PY_VERSION_HEX`` / version
   tuples.
#. Echion: compile on target Python; fix ``#if`` ladders and struct/layout drift.
#. ``_asyncio.py`` and any other Python version branches.
#. ``setup.py`` and ``ddtrace/internal/settings/profiling.py`` gating.
#. Riot, requirements files, test skip cleanup.
#. **Validate all profiling features** with automated tests (matrix above) on the target Python.
#. Optional: manual / dogfood checks on a real environment.
#. Release note and final CI green.
