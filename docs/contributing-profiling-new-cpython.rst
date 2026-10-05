.. _profiling_new_cpython:

Profiling and new CPython versions
==================================

Short bring-up pointers for Continuous Profiler support on a **new CPython
minor**. Runtime for 3.15 is already on ``main`` (native ABI / asyncio /
packaging). This page is the process map only — version-specific answers live
elsewhere.

Prior art and version-specific answers:

* Prior migration example: `PR #15546`__ (Python 3.14) — framed as prior art
  only; do not treat its deltas as the answer key for a later minor.
* Latest completed-minor catalog (ABI fixes, hook choices, gotchas, PR map):
  ``docs/cpython-diffs/py315_pr_catalog.md``.
* Header-diff notes for that minor: ``docs/cpython-diffs/analysis_314_to_315.md``.
* Live stack status: ``scripts/py315-stack/PROFILING_STACK.md``.
  Keep that file current; do not duplicate a status table here.

__ https://github.com/DataDog/dd-trace-py/pull/15546

PEP phase timeline
------------------

Anchor every profiling CPython bump to the **active release-schedule PEP**
(for 3.15 that was `PEP 790`__; for a later minor find the successor PEP).
Confirm the tag actually shipped before treating a schedule date as done.

__ https://peps.python.org/pep-0790/

+----------+----------------------------------+------------------------------------------+
| Phase    | What to land                     | Where answers live                       |
+==========+==================================+==========================================+
| **Alpha**| Native ABI / layout contracts;   | Catalog §1; layout contract tests;       |
|          | gated CI compile                 | version registry entry                   |
+----------+----------------------------------+------------------------------------------+
| **Beta** | Collectors + asyncio hook path;  | Catalog §2; wrap / alternate hook probe; |
|          | import-degrade path              | ``scripts/run-profiling-tests``          |
+----------+----------------------------------+------------------------------------------+
| **RC**   | Images → digests → language-     | Catalog §3; hermetic pin to **exact**    |
|          | tools → optional then required   | prerelease tag                           |
|          | wheels                           |                                          |
+----------+----------------------------------+------------------------------------------+
| **Final**| SSI/OCI allow-list; classifiers; | Catalog §4; reno; ADR without memory-    |
|          | reno; ADR                        | parity claim                             |
+----------+----------------------------------+------------------------------------------+

Optional local smoke
--------------------

``scripts/verify_profiler_compatibility.py`` and
``scripts/run-profiling-tests`` use
``scripts/profiles/profiling_versions.json``. Keep ``default_python`` aligned
with a minor that ``tests/profiling/suitespec.yml`` actually runs (today
**3.15** for profile / profile-memalloc). Pass ``--python`` to target another
installed interpreter.

Follow-ups (not this page)
--------------------------

Full process depth, agentic / orchestrated migration, engraver/Quay/staging
A/B playbooks, and ``cpython_delta`` inventory tooling are **out of scope**
here. Track those as separate follow-ups (e.g. #19273 for fuller tooling,
#20565 for ``cpython_delta``).

Links
-----

* Catalog: ``docs/cpython-diffs/py315_pr_catalog.md``
* Header analysis: ``docs/cpython-diffs/analysis_314_to_315.md``
* Stack map: ``scripts/py315-stack/PROFILING_STACK.md``
* Version registry: ``scripts/profiles/profiling_versions.json``
* Compat baselines: ``scripts/profiles/compatibility_baselines.json``
* Parent tracker: `#17809`__ / `#17817`__

__ https://github.com/DataDog/dd-trace-py/issues/17809
__ https://github.com/DataDog/dd-trace-py/issues/17817
