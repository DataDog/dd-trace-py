Test Impact Analysis
====================

``DD_TIA_ENABLED=1 pytest`` activates coverage-based dependency recording and
selection through ``ddtrace.testing``. TIA consumes the existing per-test
``dd-coverage`` data used by ITR, including executed repository files and the
collector's transitive import-time dependencies. A small
``importlib.import_module`` wrapper supplies dependency roots for cached dynamic
imports, which execute neither module code nor import bytecode. These roots
are expanded by dd-coverage; ``builtins.__import__`` is left unchanged. When ITR
coverage is also enabled, both features share the same test execution context;
TIA alone does not enable per-test coverage uploads.

The process-spawn guard, repository path filter, and file selector are adapted
from `pytest-filemon <https://github.com/gnufede/pytest-filemon>`_ under
Apache-2.0 (see ``LICENSE.Apache`` at the repository root). Coverage collection,
thread and multiprocessing propagation, Git commands, configuration access,
HTTP transport, authentication, retries, and plugin lifecycle reuse ddtrace code.

Maps are local-first. Before querying ``lookup``, the plugin checks
``.testoptimization/tia/<configuration_hash>/<commit>.json`` inside Git's
metadata directory (normally ``.git``). It searches the 20 most recent local
commits, newest first, and uses the first compatible map. Missing, unreadable,
corrupt, and incompatible cache entries fall back to the backend. Valid
backend responses and newly generated maps are cached using ddtrace's atomic
JSON writer. A failed remote upload does not discard the local map. Local
cache failures do not prevent backend lookup or upload. These files live
outside the tracked worktree, so caching does not make a clean checkout dirty.

The map covers repository files executed by tests and fixtures, augmented with
the collector's import-time dependencies. It does not track arbitrary data-file
reads or code outside the repository. Repository pytest plugins, including
conftest files and modules registered through ``pytest_plugins``, and their
dependency closures conservatively affect every test, including cached fixture
dependencies. Dynamic imports during collection and fixture setup also supply
shared roots; pytest's test-module imports remain per-test.
Repository modules loaded before instrumentation are treated conservatively as
shared dependencies. A test without observed coverage always runs. Joined
threads and supported multiprocessing children use dd-coverage's propagation.
The collector uses its existing file-level or line-level mode, selected by
``_DD_COVERAGE_FILE_LEVEL``. A collector installed by another caller is reused
and left installed when TIA finishes. Subprocess
creation makes a test always run; subprocesses created during collection or
fixture setup make all recorded tests always run. Missing, malformed, or
incompatible maps and failed Git queries run all tests. Changed files outside
the recorded dependency set also run all tests, covering new source files and
configuration which can change import resolution.

Only finished tests receive fresh entries. Previously failed tests run again;
unexecuted tests cannot acquire a fresh baseline. Maps from dirty worktrees,
collection-only runs, interrupted sessions, and collection errors are not
uploaded. The worktree must be clean both before and after execution and HEAD
must remain unchanged. TIA replaces server-side ITR skipping for its session.
The recorder identity is part of the configuration hash, so maps produced by
an older recorder or an incomplete dependency-recording revision are not
reused. The plugin runs in a single pytest process; distributed xdist, offline,
discovery, and all-flaky-test modes keep their existing behavior.

Artifact transport
------------------

Both endpoints use the existing API-subdomain connector with API-key-only
agentless authentication or the Agent EVP proxy. For staging agentless requests,
set ``DD_CIVISIBILITY_AGENTLESS_ENABLED=1``, ``DD_SITE=datad0g.com``, and
``DD_API_KEY``; requests go to ``https://api.datad0g.com`` with ``DD-API-KEY``.
No application key is needed.

Metadata is derived from the existing Test Optimization identity:

* ``repository_id`` is ITR's sanitized ``git.repository_url`` tag, honoring
  CI and user overrides, with the local Git remote as fallback.
* ``suite_id`` is the SHA-256 hexadecimal digest of the existing pytest
  ``test_command``, including ``PYTEST_ADDOPTS``.
* ``env_hash`` is the SHA-256 hexadecimal digest of the invocation arguments,
  ``PYTEST_ADDOPTS``, pytest ini configuration, repository-relative working and
  pytest root directories, runtime configuration tags, Python version, pytest
  version, and map format version.
* ``language`` is ``python``; ``compatibility_version`` defaults to ``0``.
* Runtime configurations use the same flat tags as ITR and are included in
  ``env_hash``. The optional ``configurations`` metadata field is omitted from
  both upload and lookup: the Agent's decoded-query allowlist rejects the
  punctuation in a JSON object. This keeps artifact identity consistent across
  agentless and EVP sessions without losing configuration isolation.

``PUT /api/unstable/ci/tia/artifacts`` sends the dependency document as raw
UTF-8 JSON bytes with ``Content-Type: application/octet-stream``. Metadata and
``git_commit_sha`` are query parameters. Bodies larger than 5 MiB are not
uploaded. A successful upload returns HTTP 201 with a JSON:API
``ci_app_tia_artifact_upload`` resource whose
``data.attributes`` contains ``blob_id`` and ``deduplicated``. The raw body is not
wrapped in a JSON:API envelope and is not gzip-compressed.

``POST /api/unstable/ci/tia/artifacts/lookup`` sends a JSON:API request with
the ``ci_app_tia_artifact_lookup`` resource type, a generated request ID,
metadata in ``data.attributes``, and ``git_commit_shas`` in nearest-first order.
The plugin sends its 20 most recent
local commits, without a date cutoff or extra fetch; the transport accepts up
to 100 commits. HTTP 404 is an ordinary miss and runs all tests when no local
map was available. HTTP 200 returns the stored UTF-8 JSON bytes with
``Content-Type: application/json`` and two headers:

* ``x-dd-tia-git-commit-sha`` identifies the matched commit, which must be one
  of the requested SHAs. The local cache indexes the response by this commit.
* ``x-dd-tia-source-commit`` identifies the commit that produced the database.
  Git diffs always use this source commit, even when it differs from the match
  or is older than the lookup history. Missing local source history runs all
  tests.

The dependency document has the following structure::

    {
      "base_commit": "<source sha>",
      "map": {
        "version": 1,
        "files": ["src/module.py", "tests/test_module.py"],
        "tests": {"tests/test_module.py::test_example": [0, 1]},
        "import_deps": [],
        "failed": [],
        "always": []
      }
    }

The source response header overrides any ``base_commit`` inside a retrieved
blob. Files use repository-relative POSIX paths, and test keys are pytest node
IDs. ``tests`` and ``import_deps`` reference indices into ``files``; ``failed``
and ``always`` contain node IDs. Git diffs include both rename paths, deletions,
working-tree modifications, and nonignored untracked files.

The artifact endpoints must be enabled for the organization; a disabled
endpoint returns HTTP 404. Transport failures do not change the pytest result.
Git compares against the map's source commit for both local and remote maps, so neither relies on a
machine-specific filesystem-mtime snapshot. Other Test Optimization requests,
including fetching session settings and reporting test results, continue to
use the backend when a local map avoids the TIA lookup request.

Debugging artifact reuse
------------------------

``DD_TRACE_DEBUG=1`` logs local map cache hits and misses, successful backend
restores with their matched and source commits, and the artifact POST/PUT
response status codes. These messages contain no request headers or artifact
bodies. A compatible local map avoids the lookup POST; a clean, completed
session can still upload its map with PUT. To verify remote restoration, use a
fresh clone with the same repository URL and pytest invocation and no local
TIA cache. A lookup returning HTTP 200 followed by deselection demonstrates
that the backend supplied the map.

Synthetic end-to-end scenario
-----------------------------

``tests/testing/internal/pytest/test_pytest_tia_e2e.py`` runs fresh pytest
processes against an eight-case corpus in a real Git repository. It reuses the
Test Optimization HTTP test server and exercises artifact PUT/lookup requests
over localhost. The ten-phase scenario runs in agentless mode and with EVP v2
and v4 simulations. The EVP simulations apply the Agent's query allowlist,
subdomain routing header, and API-key injection, and exercise proxy discovery
through ``/info``. They do not run a real Agent or call a live Datadog backend.
Discover the testing environment, then select only the scenario::

    scripts/run-tests --list tests/testing/internal/pytest/test_pytest_tia_e2e.py
    scripts/run-tests --venv <testing-environment-hash> -- -n 0 -k test_tia_ddcoverage_end_to_end

The scenario records eight tests initially. An unchanged run selects only the
subprocess case. A regression in ``shop/cart.py`` selects its two parametrized
cases and the subprocess case; another unchanged run retains those failed
cases. Fixing the cart clears their failure state. A fresh checkout restores
an artifact whose matched commit differs from its source commit and still
selects the affected cart cases. A cached dynamic import regression selects
both the static and dynamic consumers, and its fix clears their failures.
Changing a session fixture dependency registered through ``pytest_plugins``
runs all eight tests and exposes failures in all three fixture consumers;
restoring it clears those failures. TIA alone does not send ITR coverage uploads.

Each run writes ``.cache/tia-e2e/run-*/summary.json``, subprocess logs, and two
map renderings: ``map-current.json`` and ``map-ruby-shaped.json``. The latter
is a comparison view of the same dependency graph, using a framework scope,
a list of suite/name/parameters test records, ``global_files``, and test-array
indices in ``always_run``. Pytest's scoped node IDs distinguish parametrized
cases; argument values cannot be reconstructed from the current map, so the
comparison's ``parameters.arguments`` stays empty. The wire format is unchanged.

The summary reports compact JSON sizes and diagnostic gzip sizes. Gzip is not
used by artifact upload. The list-based view contains additional identity
metadata, so the sizes compare complete representations rather than file-index
encoding alone. This small corpus is not a scaling benchmark. A shared schema
must also define how previously failed tests stay runnable and how test-array
indices remain valid when records are reordered or merged.
