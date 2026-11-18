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
corrupt, and incompatible cache entries fall back to the backend. In a shallow
checkout, a local miss first attempts to fetch full history and rechecks the
local cache before querying the backend. A local hit with its source commit
already available needs no fetch. Valid
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
the recorded dependency set do not force a full run. New tests still run, and
changes to loaded repository pytest plugins (including newly added conftest
files) or the active pytest configuration affect all tests. Other new files
that change import resolution without a recorded dependency are not detected.

Only finished tests receive fresh entries. Previously failed tests run again;
unexecuted tests cannot acquire a fresh baseline. Maps from worktrees with tracked changes,
collection-only runs, interrupted sessions, and collection errors are not
published locally or uploaded. Tracked files must be clean both before and
after execution and HEAD must remain unchanged. Nonignored untracked files
only block publication when they occur in the recorded dependencies, such as
an imported module, test file, or pytest configuration. Unrelated generated
reports such as ``coverage.xml`` do not need to be ignored for maps to be
published. TIA replaces server-side ITR skipping for its session.
Pytest still reports unaffected tests as deselected. Datadog receives a skipped
test event for each TIA deselection, using the existing ITR skip tags, telemetry,
and session savings count, with the reason
``Skipped by Datadog Test Impact Analysis``. Fully skipped suites and modules
are reported as skipped; partially skipped suites keep their executed results.
This reporting is enabled independently of server-side ITR settings and uses
test-level counts even when server-side ITR is configured for suites. Explicit
pytest deselections (such as ``-k``) and collection-only runs do not count as TIA
savings. Reporting neither executes fixtures nor gives skipped tests fresh
dependency entries.
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
available commits, without a date cutoff; the transport accepts up
to 100 commits. Shallow history is fetched using the existing Git fetch lock
and retries with ``git fetch --unshallow`` on a local cache miss, or when a
cached map's source commit is unavailable locally. Set
``DD_CIVISIBILITY_GIT_UNSHALLOW_ENABLED=false`` to disable this fetch. A failed
fetch leaves lookup using the available commit history. HTTP 404 is an ordinary miss and runs all tests when no local
map was available. HTTP 200 returns the stored UTF-8 JSON bytes with
``Content-Type: application/json`` and two headers:

* ``x-dd-tia-git-commit-sha`` identifies the matched commit, which must be one
  of the requested SHAs. The local cache indexes the response by this commit.
* ``x-dd-tia-source-commit`` identifies the commit that produced the database.
  Git diffs always use this source commit, even when it differs from the match
  or is older than the lookup history. Source history still missing after the
  fetch attempt runs all tests.

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

Artifact request telemetry
--------------------------

Both endpoints use the existing Test Optimization request telemetry collector.
The ``civisibility`` namespace contains ``tia_artifact_lookup`` and
``tia_artifact_upload`` metric prefixes, each with:

* ``request_seconds``: per-attempt HTTP latency in seconds, including reading
  the response, excluding retry backoff.
* ``request_bytes`` and ``response_bytes``: HTTP body sizes in bytes, excluding
  headers. Compressed responses report their compressed size with the
  ``rs_compressed:true`` tag; streamed responses without ``Content-Length``
  use the received body size.
* ``request`` and ``request_errors``: request attempt and error counts. Error
  counts carry the existing ``error_type`` tag. Lookup HTTP 404 is a normal
  miss and does not increment the error count.

Retries produce a sample for each attempt. A local cache hit produces no
lookup request metrics. Metrics contain no repository or test identifiers,
artifact contents, or credentials.

Debugging artifact reuse
------------------------

``DD_TRACE_DEBUG=1`` logs local map cache hits and misses, successful backend
restores with their matched and source commits, and the artifact POST/PUT
response status codes. These messages contain no request headers or artifact
bodies. A compatible local map avoids the lookup POST; an eligible completed
session can still upload its map with PUT. To verify remote restoration, use a
fresh clone with the same repository URL and pytest invocation and no local
TIA cache. A lookup returning HTTP 200 followed by deselection demonstrates
that the backend supplied the map.

To debug dd-trace-py's own suites through the existing test runner, use
``scripts/ci_visibility/tia_debug.sh``::

    scripts/run-tests --list tests/internal/test_logger.py
    export DD_API_KEY='<staging API key>'
    export DD_SITE=datad0g.com
    scripts/ci_visibility/tia_debug.sh --venv <Python-3.12+-environment-hash> -- --ddtrace -n 0

The wrapper enables TIA, focused Test Optimization debug logging, and agentless
requests to ``DD_SITE`` (default ``datad0g.com``). Set ``DD_TRACE_DEBUG=1`` for
broader tracer logging. It forwards runner and pytest arguments unchanged and
reuses existing environments unless ``-r`` is supplied. ``scripts/ddtest``
passes the TIA settings and API key into Docker and Podman by variable name,
so the key does not appear in the printed runner command. Export credentials
rather than editing tracked files, because tracked edits prevent map publication.
Repeat the same invocation to check reuse, and inspect the output for ``TIA:``
and ``TIA artifact`` messages. The wrapper leaves the local map cache in place.
The internal suite defaults to xdist; ``-n 0`` overrides that default so TIA can
run. Omit this option for environments without pytest-xdist. Avoid explicit
``-k``, ``-m``, ``--lf``, ``--ff``, and node-ID selectors when investigating TIA
selection, because those selectors keep all tests selected by pytest runnable.

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

Staging end-to-end script
-------------------------

``scripts/ci_visibility/tia_staging_e2e.py`` runs normal
``DD_SITE=datad0g.com DD_TIA_ENABLED=1 pytest --ddtrace`` sessions against
staging, using the active Python 3.12+ environment. Install this branch's ddtrace
and pytest there, and supply ``DD_API_KEY`` in the environment or in the blank
setting near the top of the script. ``--python`` selects a different Python.
The script enables agentless mode so the API key is sent directly to staging.

``--prepare-only`` creates the eight-case shop corpus and prints a command for
running pytest manually. The default run checks initial recording, unchanged
selection, affected failures and recovery, a fresh clone retrieving its map
from staging, cached dynamic imports, and shared fixture dependencies. Corpus
repositories, clones, maps, subprocess logs, and ``summary.json`` remain under
``.cache/tia-staging/run-*/``. Saved logs redact the API key. Committed copies
of the script must keep the API-key setting empty.
