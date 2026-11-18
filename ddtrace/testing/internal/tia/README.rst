Test Impact Analysis
====================

``DD_TIA_ENABLED=1 pytest`` activates import-based dependency recording and
selection through ``ddtrace.testing``. The import monitor, repository path
filter, and file selector are adapted from
`pytest-filemon <https://github.com/gnufede/pytest-filemon>`_ under Apache-2.0
(see ``LICENSE.Apache`` at the repository root). They add no dependency on the
external pytest plugin. Git commands, configuration access, HTTP transport,
authentication, compression, retries, and plugin lifecycle reuse ddtrace code.

Maps are local-first. Before querying ``lookup``, the plugin checks
``.testoptimization/tia/<configuration_hash>/<commit>.json`` inside Git's
metadata directory (normally ``.git``). It searches the 20 most recent local
commits, newest first, and uses the first compatible map. Missing, unreadable,
corrupt, and incompatible cache entries fall back to the backend. Valid
backend responses and newly generated maps are cached using ddtrace's atomic
JSON writer. A failed remote upload does not discard the local map. Local
cache failures do not prevent backend lookup or upload. These files live
outside the tracked worktree, so caching does not make a clean checkout dirty.

The map covers repository files imported by tests, fixtures, and collection.
It does not track arbitrary data-file reads or code outside the repository.
Shared fixture and conftest dependencies conservatively affect every test.
Cached imports are recorded, including transitive module edges. Subprocess
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
The first implementation runs in a single pytest process; distributed xdist,
offline, discovery, and all-flaky-test modes keep their existing behavior.

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
* ``configurations`` contains the same flat runtime configuration tags as ITR.

``PUT /api/unstable/ci/tia/artifacts`` sends the dependency document as raw
UTF-8 JSON bytes with ``Content-Type: application/octet-stream``. Metadata and
``git_commit_sha`` are query parameters; ``configurations`` is a URL-encoded
JSON object. Bodies larger than 5 MiB are not uploaded. A successful upload
returns HTTP 201 with ``blob_id`` and ``deduplicated``. The raw body is not
wrapped in a JSON:API envelope and is not gzip-compressed.

``POST /api/unstable/ci/tia/artifacts/lookup`` sends a JSON:API request with
ITR's ``test_params`` resource type, metadata in ``data.attributes``, and
``git_commit_shas`` in nearest-first order. The plugin sends its 20 most recent
local commits, without a date cutoff or extra fetch; the transport accepts up
to 100 commits. HTTP 404 is an ordinary miss and runs all tests when no local
map was available. HTTP 200 returns raw UTF-8 JSON bytes and two headers:

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

Transport failures do not change the pytest result. Git compares against the
map's source commit for both local and remote maps, so neither relies on a
machine-specific filesystem-mtime snapshot. Other Test Optimization requests,
including fetching session settings and reporting test results, continue to
use the backend when a local map avoids the TIA lookup request.
