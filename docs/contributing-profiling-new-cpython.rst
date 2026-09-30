.. _profiling_new_cpython:

Profiling and new CPython versions
==================================

This guide is for maintainers who add support for a **new CPython minor release** (e.g. 3.15) across
**everything dd-trace-py owns in the Continuous Profiler product**: **stack** (CPU / wall samples,
Echion), **asyncio** integration for stack and tasks, **lock** profilers (threading + asyncio),
**memory** and **heap** (memalloc), **exception** profiling, **PyTorch** hook, **ddup** export, build
gates, Riot/CI, and **validation tests** for each area.

Reference implementations:

* `PR #15546`__ (feat(profiling): support Python 3.14) — Echion frame/task/asyncio changes,
  ``setup.py`` un-gating, profiling defaults, Riot venv splits, tests, and a release note.
* `PR #19269`__ (native ABI) + `PR #19270`__ (collectors/CI) + `PR #19272`__
  (asyncio) — Echion ``PyFrameState`` / ``FRAME_OWNED_BY_CSTACK``; ``setup.py``
  native-build un-gating; ``_asyncio.py`` uses ``sys.monitoring`` on 3.15 because
  wrapping is unavailable (the API itself exists since 3.12).

__ https://github.com/DataDog/dd-trace-py/pull/15546
__ https://github.com/DataDog/dd-trace-py/pull/19269
__ https://github.com/DataDog/dd-trace-py/pull/19270
__ https://github.com/DataDog/dd-trace-py/pull/19272

Live bring-up status (which PRs are merged vs open) lives in
``scripts/py315-stack/PROFILING_STACK.md`` — keep that file current; do not
duplicate a "current status" table here.

Version hex quick reference
---------------------------

.. code-block:: text

   Python 3.11  →  0x030b0000
   Python 3.12  →  0x030c0000
   Python 3.13  →  0x030d0000
   Python 3.14  →  0x030e0000
   Python 3.15  →  0x030f0000   ← current bring-up
   Python 3.16  →  0x03100000   ← next target

PEP phase timeline
------------------

Anchor every profiling CPython bump to the **active release-schedule PEP**
(for 3.15 that was `PEP 790`__; for 3.16 find the successor PEP). Confirm the
tag actually shipped before treating a schedule date as done. Use the
``track-cpython-release-schedule`` skill/flow so engraver / images /
language-tools pins land **within days** of each RC or final — not weeks.

__ https://peps.python.org/pep-0790/

+----------+----------------------------------+------------------------------------------+
| Phase    | What to land                     | Automate / verify                        |
+==========+==================================+==========================================+
| **Alpha**| Native ABI / layout contracts;   | ``cpython_delta`` inventory+diff (when   |
|          | gated (``allow_failure``) CI     | available); layout contract tests;       |
|          | compile job; pyo3 bump if        | ``verify_profiler_compatibility.py       |
|          | needed                           | --scaffold 3.X``; catalog §1             |
+----------+----------------------------------+------------------------------------------+
| **Beta** | Collectors + asyncio hook path;  | wrap() probe on ``create_task``;         |
|          | monitoring multiplexer if wrap   | ``scripts/run-profiling-tests``;         |
|          | fails; import-degrade path       | catalog §2                               |
+----------+----------------------------------+------------------------------------------+
| **RC**   | Engraver ``python/3.X.YrcN``      | Within days of each RC: engraver →       |
|          | (+fips) → digests →              | digests → language-tools → IMAGE_TAG;    |
|          | language-tools → manylinux       | pin hermetic interpreter to **exact**    |
|          | mirror → optional then required  | prerelease tag; Cython upper bound;      |
|          | cp3XX wheels                     | catalog §3                               |
+----------+----------------------------------+------------------------------------------+
| **Final**| SSI/OCI allow-list; classifiers; | SSI stays ``when: never`` until final;   |
|          | reno; ADR                        | never publish SSI from the wheel PR;     |
|          |                                  | catalog §4                               |
+----------+----------------------------------+------------------------------------------+

Expected changes (in order)
---------------------------

These are the **planned** profiling PRs for a new minor. Py-315 artifacts in
parentheses; each row says how to automate next time.

#. **Native ABI / build path** (`PR #19269`__) — Echion ``PyFrameState`` /
   ``FRAME_OWNED_BY_CSTACK`` / cmake layout contracts. Automate: run
   ``cpython_delta`` on ``OLD..NEW``; require ``test_frame_state_XXX.cpp`` /
   ``test_cpython_layout_contracts.cpp`` entries.
#. **Asyncio hook path** (`PR #19272`__) — On 3.15+, ``_asyncio.py`` uses
   ``sys.monitoring`` because ``wrap()`` is unavailable for ``create_task``;
   keep ``wrap`` below the new minor. Automate: skill checklist + wrap probe
   (not header-only ABI diff).
#. **Dev tooling / runbook** (this PR, `PR #19273`__) — baselines JSON,
   ``verify_profiler_compatibility.py``, ``run-profiling-tests``, this guide,
   PR catalog. Automate: copy checklist; scaffold version registry entry.
#. **prof-correctness gating** (`PR #19207`__ + prof-correctness
   ``python_*_3.X`` jobs) — automatic correctness gate on profiling PRs.
   Automate: template ``python_*_3.X`` jobs + compare gate vs previous minor.
#. **Required wheels + lib_injection schedule** (`PR #20450`__) — after
   Cython pin and optional platforms (`PR #19861`__). Automate: optional →
   required platform template; schedule lib_injection once wheels exist.
#. **Release note** — customer-facing "profiling supports 3.X". Automate:
   ``releasenote`` skill.
#. **ADR** (`PR #20478`__) — functional readiness claim; **do not** claim
   memory parity. Automate: skill checklist; human judgment on GO/NOGO.

__ https://github.com/DataDog/dd-trace-py/pull/19269
__ https://github.com/DataDog/dd-trace-py/pull/19272
__ https://github.com/DataDog/dd-trace-py/pull/19273
__ https://github.com/DataDog/dd-trace-py/pull/19207
__ https://github.com/DataDog/dd-trace-py/pull/20450
__ https://github.com/DataDog/dd-trace-py/pull/19861
__ https://github.com/DataDog/dd-trace-py/pull/20478

Unexpected changes and gotchas
------------------------------

What bit us on 3.15. Every item: symptom → fix → automate. Full PR rows live in
``docs/cpython-diffs/py315_pr_catalog.md`` ("Top unexpected gotchas").

* **``wrap()`` bytecode patching broke for asyncio ``create_task``.** Move
  asyncio to ``sys.monitoring`` on the new minor while keeping ``wrap`` below
  it (`PR #19272`__). Multiplexer (`PR #19247`__) is a prerequisite. A wrong
  fail-closed gate had to be fixed — verify the gate matches the hook path.
  Automate: skill checklist + wrap probe (not ``cpython_delta`` alone).

* **Prerelease ABI mismatch (a2 vs rc2).** CrashLoop / ``_native`` import
  errors that look like profiler bugs. Pin the hermetic interpreter and
  prof-correctness images to the **exact** prerelease tag. Automate: version
  registry / image pin checklist; never reuse an older alpha when building for
  an RC.

* **Cython ≥ 3.3 on the new version.** Pin Cython below 3.3; start cp3XX
  wheels **optional** (`PR #19861`__), then promote to required
  (`PR #20450`__). Dead end: widening ``requires-python`` alone
  (`PR #19865`__). Automate: Cython upper-bound + optional→required template.

* **Base-image / engraver flow for wheels.** Engraver
  ``python/3.X.YrcN{,-fips}`` → dd-source engraver digests → language-tools
  seed → manylinux mirror + IMAGE_TAG bump (`PR #17959`__, images#11732) →
  Rapid ``whl_installer`` host-pip / ``locate_file`` fix (dd-source#98654).
  Automate: ``track-cpython-release-schedule`` within days of each RC.

* **Staging A/B failures are auth/signing/wheels, not the profiler.**
  Preflight ``BUILD_WEDGED``, AppGate/vault expiry, SSH ``unknown_key``, and
  passphrase prompts that hang in detached tmux. Automate: staging preflight
  checklist before blaming profiling code.

* **Memory:** RSS is about **+15%** with the profiler on (~53% runtime /
  ~47% profiler). memalloc is not the lever. **Do not** gate the functional
  claim on memory parity (ADR `PR #20478`__).

* **SSI/OCI stays ``when: never`` until final.** Do not publish SSI from the
  wheel PR (`PR #17977`__ dead end). Automate: hard stop in the migration
  skill.

* **Hermetic pip / PEP 440 local versions.** Package verify rejects local
  versions during prerelease wheels (`PR #20474`__). Automate: accept
  PEP 440-equivalent local versions in verify-package-version.

__ https://github.com/DataDog/dd-trace-py/pull/19272
__ https://github.com/DataDog/dd-trace-py/pull/19247
__ https://github.com/DataDog/dd-trace-py/pull/19861
__ https://github.com/DataDog/dd-trace-py/pull/20450
__ https://github.com/DataDog/dd-trace-py/pull/19865
__ https://github.com/DataDog/dd-trace-py/pull/17959
__ https://github.com/DataDog/dd-trace-py/pull/20478
__ https://github.com/DataDog/dd-trace-py/pull/17977
__ https://github.com/DataDog/dd-trace-py/pull/20474

Preparation: what tends to break
---------------------------------

When CPython bumps, expect changes in:

* ``_PyInterpreterFrame`` and related **internal headers** (include paths move between releases;
  fields may become ``_PyStackRef``, ``stackpointer`` vs ``stacktop``, ``localsplus`` layout).
* **Tagged pointers** on frame/code objects (recover ``PyObject*`` per upstream notes, e.g.
  ``python/cpython#123923`` for 3.14).
* **Asyncio** C layout: ``FutureObj`` / ``TaskObj`` struct layout and where **native** tasks live
  (e.g. per-thread / per-interpreter linked lists and ``asyncio_tasks_head`` in 3.14+).
* **Python-visible** asyncio: policy class renames, whether ``_scheduled_tasks`` /
  ``_eager_tasks`` are exported from the C module or live only in Python.
* **Free-threaded builds** (``Py_GIL_DISABLED``): from 3.14, struct layouts diverge for nogil
  builds (e.g. ``task_tid`` in ``TaskObj``). Guard with ``#ifdef Py_GIL_DISABLED`` where needed.
  On Windows, ``Py_GIL_DISABLED`` must now be set explicitly by the build backend; it is no
  longer inferred automatically.

Read the PR #15546 description for the concrete 3.14 deltas before extrapolating to the next
version.

Discover CPython deltas (before writing code)
---------------------------------------------

1. Use the **compare-cpython-versions skill** first — it runs a systematic diff of the headers
   we depend on between two CPython tags (e.g. ``v3.14.0`` → ``v3.15.0`` or ``main``). Run it
   before opening any source file:

   .. code-block:: text

      # Via the Skill tool:
      compare-cpython-versions  (previous: 3.14, target: 3.15)

2. If you need to manually inspect or regenerate the diff, clone **python/cpython** (the
   skill uses ``~/dd/cpython`` by convention) and diff the headers we depend on:

   .. code-block:: bash

      # Clone once (or fetch tags on an existing checkout)
      git clone https://github.com/python/cpython.git ~/dd/cpython
      cd ~/dd/cpython && git fetch --tags

      # Diff all headers relevant to echion/profiling between two releases
      # Adjust tag names to actual release tags (e.g. v3.14.0, v3.15.0 or main)
      git diff v3.14.0 v3.15.0 -- \
        Include/cpython/genobject.h \
        Include/internal/pycore_frame.h \
        Include/internal/pycore_interpframe.h \
        Include/internal/pycore_interpframe_structs.h \
        Include/internal/pycore_llist.h \
        Include/internal/pycore_runtime.h \
        Include/internal/pycore_stackref.h \
        Include/internal/pycore_tstate.h \
        Modules/_asynciomodule.c

   Key files to watch (paths can move between releases — verify they exist on the target tag):

   * ``Include/internal/pycore_interpframe_structs.h``, ``pycore_frame.h``,
     ``pycore_interpframe.h``, adjacent ``pycore_*`` headers.
   * ``Include/cpython/genobject.h`` and anything **PyGen_\*** / yield-from paths used in
     Echion.
   * ``Modules/_asynciomodule.c`` — only the struct/typedef section matters
     (``FutureObj_HEAD``, ``TaskObj``, ``_Py_AsyncioModuleDebugOffsets``); function bodies
     are not relevant to echion.
   * ``Include/internal/pycore_tstate.h``, ``pycore_llist.h``, ``pycore_stackref.h``,
     ``pycore_runtime.h`` (all became relevant in 3.14).

   A committed reference diff for 3.13 → 3.14 lives at
   ``docs/cpython-diffs/cpython_313_to_314_headers.diff`` in the ``DataDog/echion`` repo.

3. In **dd-trace-py**, use the **find-cpython-usage skill** to enumerate every internal header
   and struct the codebase currently touches:

   .. code-block:: text

      # Via the Skill tool:
      find-cpython-usage

4. **Version hex:** Python 3.15 is gated with ``PY_VERSION_HEX >= 0x030f0000``. Keep older
   release guards (e.g. ``0x030e0000`` for 3.14) and only add a new branch when behavior or
   layout **diverges** from the prior release.

Quick grep in dd-trace-py (find prior-version guards):

.. code-block:: bash

   rg 'PY_VERSION_HEX|0x030e' ddtrace/internal/datadog/profiling ddtrace/profiling setup.py
   rg '3, 14|3\\.14' tests ddtrace setup.py riotfile.py

Native stack profiler (Echion) — layout in this repo
-----------------------------------------------------

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
| Stack chunk (frame iter)  | ``stack/src/echion/stack_chunk.cc``      |
+---------------------------+------------------------------------------+
| Task / Future layouts     | ``stack/echion/echion/cpython/tasks.h``  |
+---------------------------+------------------------------------------+
| Asyncio task enumeration  | ``stack/echion/echion/tasks.h``,         |
|                           | ``stack/echion/echion/threads.h``,       |
|                           | ``stack/src/echion/threads.cc``          |
+---------------------------+------------------------------------------+
| Misc guards               | ``stack/echion/echion/state.h``,         |
|                           | ``stack/echion/echion/greenlets.h``      |
+---------------------------+------------------------------------------+

Build against the **target** interpreter first and fix compile errors. Then run automated tests
for **stack** and **asyncio** (see `Validate all profiling features`_).

For C/C++ conventions and safety expectations, see ``.cursor/rules/native-code.mdc``
(if present).

Python-side integration
-----------------------

* ``ddtrace/profiling/_asyncio.py`` — event-loop policy names, weak sets for
  scheduled/eager tasks, version-guarded access patterns.
* Search under ``ddtrace/profiling/`` for ``sys.version_info``, ``PY_MAJOR_VERSION``, and
  similar.

Build and product gating
------------------------

* ``setup.py`` — Ensure **memalloc**, **ddup**, and **stack** CMake extensions (and Rust
  profiling features, if gated) are **not** skipped on the new Python version. PR #15546
  **removed** ``sys.version_info < (3, 14)`` style exclusions; do the same for ``(3, 15)``
  when enabling 3.15. Add a **new** upper bound only if a **future** version is known broken.

* ``ddtrace/internal/settings/profiling.py`` — Remove any "force stack profiler off on X.Y"
  guards. Keep **ddup** load failures honest: log and disable profiling when the extension
  truly fails to import.

CI, Riot, and dependencies
--------------------------

* ``riotfile.py`` — Add or extend ``Venv(pys="3.15", ...)`` where a new Python needs different
  pins (examples from 3.14 work: **uwsgi**, **protobuf**, **gevent**, memalloc/**lz4** quirks).
  Follow existing patterns for ``select_pys`` and comments explaining version caps.

* Regenerate ``.riot/requirements/*.txt`` when adding venvs (same workflow as other Python
  bumps).

* Grep tests: ``3.14``, ``3, 14``, ``max_version``, profiling-related ``skip``.

Wheel build images (manylinux / musllinux)
------------------------------------------

Linux wheels are built inside PyPA manylinux / musllinux images mirrored into
``registry.ddbuild.io`` via ``DataDog/images``. The mirrored image must contain a
``cp3XX-cp3XX`` interpreter for every Python version in the wheel matrix. When a new CPython
minor is added you have to bump the pinned image tag once upstream PyPA ships it.

Where the image tags are referenced in this repo:

* ``.gitlab/package.yml`` — ``MANYLINUX_AMD64_IMAGE_TAG``, ``.AARCH64_IMAGES``,
  ``.X86_64_IMAGES``, plus the hard-coded ``IMAGE_TAG`` entries inside the upload-job
  ``needs:`` blocks.
* ``.gitlab/benchmarks/microbenchmarks.yml`` — ``PACKAGE_IMAGE`` plus the literal
  ``needs:`` job-name string that embeds the image tag.
* ``.gitlab/benchmarks/macrobenchmarks.yml`` — same ``needs:`` job-name string.
* ``.gitlab-ci.yml``, ``.gitlab/multi-os-tests.yml``, ``.gitlab/system-tests.yml``,
  ``.gitlab/debugging-exploration.yml`` — additional ``IMAGE_TAG`` references for non-wheel
  jobs that run inside the manylinux image. Bump these in lockstep.

Find the right PyPA base tag
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Step 1 — list candidate Quay tags newest-first:

.. code-block:: bash

   curl -s 'https://quay.io/api/v1/repository/pypa/manylinux2014_x86_64/tag/?limit=20&onlyActiveTags=true' \
     | python3 -c "import json,sys; [print(t['name'], t['last_modified']) for t in json.load(sys.stdin).get('tags',[])]"

The tags follow ``YYYY.MM.DD-N``. ``latest`` always points at the newest.

Step 2 — confirm the target ``cp3XX`` interpreter is built into the image. The authoritative
source is ``pypa/manylinux``'s ``docker/Dockerfile`` on the commit corresponding to the Quay
tag. Either ``docker run --rm quay.io/pypa/manylinux2014_x86_64:<TAG> ls /opt/python`` and grep
for ``cp3XX``, or — if Docker is unavailable — read the Dockerfile directly:

.. code-block:: bash

   # Find the commit that added the cpython version you need
   gh api 'repos/pypa/manylinux/commits?path=docker/Dockerfile&per_page=30' \
     --jq '.[] | "\(.sha[0:8])\t\(.commit.author.date)\t\(.commit.message | split("\n")[0])"' \
     | grep -i "cpython 3.15"

   # Inspect the current Dockerfile to see exactly which cpython versions it builds
   gh api 'repos/pypa/manylinux/contents/docker/Dockerfile' --jq '.download_url' \
     | xargs curl -sL | grep -E 'build-cpython.sh .* 3\.[0-9]+'

Any Quay tag dated after the "add CPython 3.X" commit will carry the new interpreter. Pick the
newest one.

Step 3 — confirm the same Quay tag exists across all four image variants we mirror:

.. code-block:: bash

   for img in manylinux2014_x86_64 manylinux2014_aarch64 musllinux_1_2_x86_64 musllinux_1_2_aarch64; do
     curl -s "https://quay.io/api/v1/repository/pypa/$img/tag/?specificTag=<TAG>&onlyActiveTags=true" \
       | python3 -c "import json,sys; print('$img', 'OK' if json.load(sys.stdin).get('tags') else 'MISSING')"
   done

PyPA usually publishes all four together, but verify before relying on it.

Mirror the tag, then bump dd-trace-py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **DataDog/images PR.** Edit ``mirror.yaml`` (search for the existing
   ``quay.io/pypa/manylinux2014_x86_64`` entries) and add six new entries — one per arch for
   manylinux2014 and musllinux_1_2 (x86_64, i686, aarch64) — copying the existing block's
   shape and bumping the tag. Then from the repo root::

      bzl run //image-mirroring-tooling -- update-digest quay.io/pypa/manylinux2014_x86_64:<TAG>
      # ...repeat for the other five sources

   Commit ``mirror.yaml`` and ``mirror.lock.yaml`` together. After merge, **wait for the
   master-branch mirror job** to actually push the images to ``registry.ddbuild.io/images/mirror/pypa/…``.

2. **Trigger the dd-trace-py internal image builds.** Mirroring alone doesn't produce the
   ``v<pipeline>-<sha>-<base>`` tags consumed by ``.gitlab/package.yml`` (e.g.
   ``v85383392-751efc0-manylinux2014_x86_64``). Manually re-run CI on ``DataDog/images``
   master for each of the four images (manylinux2014 x86_64/aarch64,
   musllinux_1_2 x86_64/aarch64). Record the four new ``v...`` tags.

3. **dd-trace-py PR.** Bump every reference listed at the top of this section to the new
   tags. The ``needs:`` strings in ``microbenchmarks.yml`` / ``macrobenchmarks.yml`` embed
   the literal image tag in the cross-job name; they must move in lockstep with
   ``.X86_64_IMAGES`` or ``needs:`` resolution fails.

Validate all profiling features (minor-version migration)
---------------------------------------------------------

Before merging support for a new CPython, treat **each profiler surface** as part of the
migration: ABI changes often break **stack** first, but **memalloc**, **locks**, and
**exceptions** use native or C API-adjacent code that must still pass on the new version.

Validation gates (PASS criteria)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Ordered gates. Do not claim the minor is ready until each applicable gate is green.
Py-315 source: catalog §§5–6 and the gotchas section above.

#. **Local compat smoke** — ``scripts/verify_profiler_compatibility.py --python 3.X``
   (and ``--quick`` while iterating). PASS: import/guard checks green; with full mode,
   asyncio guards + named-task pprof samples match
   ``scripts/profiles/compatibility_baselines.json`` (``--compare``). Automate: CI or
   ``scripts/run-profiling-tests --python 3.X``.

#. **Local 3.(X-1) vs 3.X A/B harness** — smoke, async, ``PROFILING=0``, and
   ``hook_path`` probe (branch ``vlad/chore-local-ab-314v315`` for 314v315; follow-up
   is to parameterize on main). PASS: both arms healthy; hook path matches the
   expected wrap vs ``sys.monitoring`` choice; **do not** require RSS parity
   (~+15% with profiler on is expected). Automate: still mostly manual — candidate
   is a version-parameterized script on main.

#. **Riot / unit profiling suites** — ``scripts/run-profiling-tests`` /
   ``scripts/run-tests`` over ``tests/profiling/`` (``profile$``,
   ``profile-memalloc``, ``profile-uwsgi``). PASS: suites green on the target
   interpreter. Automate: riot matrix entry + allow_failure→required flip.

#. **prof-correctness ``python_*_3.X`` jobs** — compare gate vs previous minor.
   Pin the gate image/wheel to the **exact** prerelease. PASS: gates green within
   theory-based margins (not one-shot empiricism). Automate: template jobs + S3
   wheel poll timeout (`PR #20444`__).

#. **Staging smoke A/B, then ai_gateway A/B chain** — after wheels exist.
   Preflight auth/signing/wheel availability first. PASS: smoke healthy, then
   ai_gateway chain; ``BUILD_WEDGED`` / SSH / vault failures are **not** profiler
   bugs. Automate: staging_ab campaign env (path in Links_); skill checklist.

__ https://github.com/DataDog/dd-trace-py/pull/20444

Automated tests (what to run)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For a quick sanity check on any Python version (import guards + pprof samples), use the
compatibility script before running the full suite:

.. code-block:: bash

   # Import/guard checks only — no C extensions required (~2 s)
   python scripts/verify_profiler_compatibility.py --python 3.15 --quick

   # Full check: asyncio guards + real pprof samples with named tasks (~8 s)
   python scripts/verify_profiler_compatibility.py --python 3.15

   # Save results as the baseline for this MAJOR.MINOR
   python scripts/verify_profiler_compatibility.py --python 3.15 --baseline

   # Compare against a saved baseline (use in CI or after a change)
   python scripts/verify_profiler_compatibility.py --python 3.15 --compare

Baselines for Python 3.9–3.14 live in ``scripts/profiles/compatibility_baselines.json``.

Use **`scripts/run-tests`** (see :ref:`testing_guidelines` in ``contributing-testing``) —
**never** raw ``pytest`` for full-suite validation. For profiling, CI maps paths to Riot via
**`tests/profiling/suitespec.yml`**: patterns such as **`profile$`**, **`profile-uwsgi`**, and
**`profile-memalloc`**.

**Feature → code → tests** (paths relative to ``ddtrace/profiling/`` or
``tests/profiling/``):

* **Stack / wall / CPU** — ``collector/stack.py`` and Echion under
  ``ddtrace/internal/datadog/profiling/stack/``. Tests: ``collector/test_stack.py``,
  ``collector/test_stack_native.py``, ``test_accuracy.py``, and the many
  ``collector/test_asyncio_*.py`` files for asyncio stack semantics.

* **Locks** — ``collector/threading.py``, ``collector/asyncio.py``, ``collector/_lock.pyx``.
  Tests: ``collector/test_threading.py``, ``collector/test_lock_reflection.py``,
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
  ``collector/test_stack_native.py`` and representative ``test_asyncio_*.py`` files while
  iterating.
* **Memalloc / heap:** run **profile-memalloc**; always include ``collector/test_memalloc.py``
  and ``collector/test_heap_tracker_count.py``.
* **Locks / threading:** use ``collector/test_threading.py`` and related asyncio lock tests
  (file is large — narrow with ``run-tests`` on touched paths during development, then full
  profile suite before merge).
* **Full profiling regression:** ``scripts/run-tests`` over ``tests/profiling/`` or let the
  script pick venvs from changed files; locally mirror CI with ``riot run …`` **profile$** /
  **profile-memalloc** / **profile-uwsgi** as needed.

**New code paths** (new env flag, CPython branch, or collector behavior) should get **unit or
subprocess tests** next to the nearest file above; follow existing patterns (many tests use
``@pytest.mark.subprocess`` and init helpers in ``tests/profiling/collector/conftest.py``).

Manual / dogfood checks (optional but recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Automation does not replace **real workloads** or **Profiling Explorer** behavior. On staging
or a one-off service, with **Python version + ddtrace commit + ``DD_PROFILING_*``** documented:

* **Stack / CPU:** visible stacks and CPU/wall samples; timeline if enabled.
* **Locks:** lock / lock-wait views; exercise ``threading`` and ``asyncio`` primitives; if
  using **``DD_PROFILING_LOCK_EXCLUDE_MODULES``**, compare with it unset vs set.
* **Memory / heap:** allocation and live-heap signal under load.
* **Exceptions:** exception profiling after controlled errors.
* **PyTorch:** small torch workload when that collector is enabled.
* **Export:** optional **``DD_PROFILING_OUTPUT_PPROF``** for local pprof inspection.

Staging service experiment (recommended for minor CPython bumps)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For migration confidence, mirror how you might run a **targeted staging rollout** (e.g. a
Cython- or profiler-related change on an internal worker/API): one **representative service**,
fixed traffic or time window, **documented** build and env.

**Goal:** Prove the new interpreter + dd-trace-py candidate do not regress **runtime health**
or **profiler signal** under real workloads — not only that unit tests pass.

**Pick a service** that stresses what you changed and what we own:

* **Stack / Echion:** mixed CPU work, deep stacks, **asyncio** (native tasks if you touched
  task enumeration), optional **gevent**/event-loop variants if the app uses them.
* **Locks:** workloads with ``threading`` and ``asyncio`` sync primitives (same idea as lock-
  profiler staging).
* **Memory / heap:** allocations + longer-lived objects if memalloc paths changed.
* **Exceptions:** paths that raise and catch often enough to see exception profiles.

**Experiment design (minimal):**

#. **Baseline arm:** current production-like combo (CPython + ddtrace version) on staging,
   same **service** and **approximate load** (QPS, soak duration).
#. **Candidate arm:** **only** CPython and/or ddtrace bump (e.g. wheel from your PR build);
   keep other deps, feature flags, and ``DD_*`` **as equal as possible**.
#. Record **commit SHAs**, **artifact** (wheel/sdist), **Python ``sys.version``**, and all
   relevant **`DD_PROFILING_*`**, **`DD_TRACE_*`**, and injection settings for both arms.

**What to watch (staging / Observability):**

* **Health:** error rate, latency, CPU/memory, restarts/crashes, OOMs.
* **Profiler product:** absence of **profiler** client errors/logs; expected **profile types**
  still arriving (CPU/wall, allocation, lock/lock-wait, exceptions, heap if enabled).
* **Profiling Explorer:** open the **same** service + environment + time range pattern for each
  arm; spot-check **flame graphs**, **lock** facets, **allocations**, **exceptions** for
  sensible stacks and no obvious holes after the version bump.

**Optional A/B on profiler knobs:** If validating a profiler-only change (e.g. lock exclude
list), run **two** candidate configs — **full wrap** vs **service-tuned excludes** — with
identical CPython and ddtrace versions so overhead/signal tradeoffs are isolated.

**Duration and rollback:** Prefer at least one **full business-day** soak or replayed load;
define **rollback** (revert image or pin) if crash rate, SLO breach, or missing profiles
exceed agreed thresholds.

**Handoff:** Paste the arm summary (versions, env, links to Explorer time ranges) into the PR
or JIRA so reviewers can reproduce the staging story.

Release notes
~~~~~~~~~~~~~

* Add a **release note** with the **releasenote** skill (``AGENTS.md``).
* Smoke / telemetry / serverless: grep for version conditionals if profiling availability
  changed (see files touched in PR #15546).

Suggested order of work
------------------------

Follow `Expected changes (in order)`_ for the PR sequence. Day-to-day steps:

#. Detect the current PEP phase (alpha / beta / RC / final) and open only the
   checklist rows that apply (`Automation checklist for 3.16`_).
#. CPython header/asyncio/``.c``/``Lib`` diff + in-repo grep for the previous
   release's ``PY_VERSION_HEX`` (``compare-cpython-versions`` /
   ``find-cpython-usage``; later ``cpython_delta`` worklist).
#. Echion (vendor copy): compile on target Python; fix ``#if`` ladders and
   struct/layout drift; add layout contract tests.
#. Asyncio hook path: probe ``wrap()`` on ``create_task``; if it fails, land
   ``sys.monitoring`` on the new minor only (keep ``wrap`` below).
#. ``setup.py`` / settings gating, Riot, Cython pin, optional then required
   wheels (RC cadence via ``track-cpython-release-schedule``).
#. **Validation gates** above (local smoke → local A/B → riot →
   prof-correctness → staging A/B).
#. Release note + ADR; SSI/OCI only at **final**.

Automation checklist for 3.16
-----------------------------

Copy-paste tracker. Replace ``3.16`` / ``0x03100000`` / ``cp316`` when starting.
Check off only after the named script or job is green. Manual items say so.

**Alpha**

* [ ] Confirm PEP schedule + clock (``track-cpython-release-schedule`` /
  ``verify-local-clock``). Manual.
* [ ] Run CPython delta inventory+diff ``v3.15.0..v3.16.0aN`` (or
  ``compare-cpython-versions`` until ``cpython_delta`` lands). Verify:
  worklist reviewed.
* [ ] Scaffold version registry / baselines
  (``python scripts/verify_profiler_compatibility.py --scaffold 3.16``).
  Verify: ``--python 3.16 --quick`` PASSes import guards.
* [ ] Native ABI + layout contracts compiled on 3.16-dev. Verify: cmake /
  gtest layout contracts green; gated CI ``allow_failure`` job compiles.
* [ ] pyo3 bump in libdatadog if limited-API requires it. Verify: crashtracker
  / flare import on 3.16.

**Beta**

* [ ] Probe ``wrap()`` on ``asyncio.create_task``. If fail: land
  ``sys.monitoring`` path for 3.16+ only; ensure multiplexer exists. Verify:
  ``hook_path`` probe + ``verify_profiler_compatibility.py --python 3.16``.
* [ ] Import-degrade path: wrapping/ModuleWatchdog must not crash apps before
  natives/wheels ready. Manual code review + import smoke.
* [ ] Collectors + riot venvs for 3.16. Verify:
  ``scripts/run-profiling-tests --python 3.16``.
* [ ] prof-correctness ``python_*_3.16`` jobs + compare vs 3.15. Verify: gate
  CI green on exact prerelease pin.

**RC**

* [ ] Within days of each RC tag: engraver ``python/3.16.YrcN{,-fips}`` →
  digests → language-tools → manylinux mirror → IMAGE_TAG. Verify:
  ``track-cpython-release-schedule`` checklist.
* [ ] Pin hermetic + prof-correctness images to **exact** rcN (never older
  alpha). Verify: no ``_native`` / CrashLoop on import.
* [ ] Cython upper-bound pin; cp316 wheels **optional**, then **required**;
  schedule lib_injection. Verify: wheel pipeline + required platforms.
* [ ] Rapid ``whl_installer`` / host-pip ``locate_file`` check on 3.16 bases.
  Manual until scripted.
* [ ] Hermetic pip accepts PEP 440 local versions. Verify: package-version job.

**Final**

* [ ] SSI/OCI allow-list (was ``when: never`` until now). Verify: SSI publish
  only after final; **not** from the wheel PR.
* [ ] Classifiers + ``requires-python`` + release note + ADR. Verify: reno
  fragment; ADR does **not** claim memory parity.
* [ ] Staging smoke A/B → ai_gateway A/B with preflight
  (AppGate/vault/SSH/signing/wheels). Verify: campaign PASS; RSS +15% OK.

**Hard stops (never skip)**

* Do not enable SSI/OCI before final.
* Do not gate functional readiness on memory parity.
* Do not skip staging preflight and blame the profiler for ``BUILD_WEDGED`` /
  auth / signing failures.
* Do not mix prerelease ABIs (aN vs rcM) in hermetic or gate images.

Links
-----

* ADR: `PR #20478`__ (functional readiness; memory parity not claimed).
* PR catalog (py-315 lessons): ``docs/cpython-diffs/py315_pr_catalog.md``.
* Header analysis (3.14→3.15): ``docs/cpython-diffs/analysis_314_to_315.md``.
* Stack map / status: ``scripts/py315-stack/PROFILING_STACK.md``.
* Compat baselines: ``scripts/profiles/compatibility_baselines.json``.
* Staging A/B playbook: ``DataDog/experimental`` ``staging_ab/`` (path only;
  lives outside this repo; see experimental PRs #11316–#11529).
* Release-schedule flow: ``track-cpython-release-schedule`` skill.
* Parent issue / volunteer tracker: `#17809`__ / `#17817`__.

__ https://github.com/DataDog/dd-trace-py/pull/20478
__ https://github.com/DataDog/dd-trace-py/issues/17809
__ https://github.com/DataDog/dd-trace-py/issues/17817

Follow-ups (out of this PR)
---------------------------

* Scheduled CI that detects a new CPython prerelease tag and opens a tracking
  issue.
* Parameterize the local 314v315 A/B harness into a ``3.X-1 vs 3.X`` script on
  main (today: ``vlad/chore-local-ab-314v315``).
* ``scripts/cpython_delta/`` inventory → diff → worklist pipeline (separate
  stacked PR).
