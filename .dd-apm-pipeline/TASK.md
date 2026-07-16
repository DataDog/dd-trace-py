# Repair the Pyramid 2.1 auto-upgrade failure (dd-trace-py #17564)

## Offline PR Context

- PR: `https://github.com/DataDog/dd-trace-py/pull/17564`
- Title: `chore: update pyramid latest version to 2.1`
- PR branch: `upgrade-latest-pyramid-version`
- PR head in this checkout: `97aa259fc1b14756e3b382ae69b6c794aa54929f`
- This branch already contains the automated upgrade. Do not redo or discard the lockfile update.

The auto-upgrade changed Pyramid lockfiles and related dependency lockfiles. Its stated purpose was:

1. Update lockfiles using `pyramid latest` to Pyramid 2.1 and dependencies.
2. Update lockfiles pinned to an earlier Pyramid version but requiring another package's latest version.

Changed files are `.riot/requirements/1336cbd.txt`, `169ce58.txt`, `26b7f73.txt`, `936e77e.txt`,
`95aa957.txt`, `97d2271.txt`, `b56d9af.txt`, `d7f052d.txt`, `scripts/integration_registry/registry.yaml`, and
`supported_versions.json`.

## Failing CI Evidence

The PR's `tracer-release / End-to-end #6 / flask-poc 6` job failed in GitHub Actions run `28986270402`, job
`86017134261`:

```text
FAILED tests/appsec/iast/sink/test_unvalidated_redirect.py::TestUnvalidatedHeader_ExtendedLocation::test_extended_location_data
AssertionError: Expected a single vulnerability with the matching criteria
assert 2 == 1
```

The failing test targets the Flask route `iast/unvalidated_redirect/test_insecure_header`. The captured trace
contains two `UNVALIDATED_REDIRECT` vulnerabilities, while the assertion requires exactly one. The job summary
was `1 failed, 324 passed, 52 skipped, 2193 deselected, 108 xfailed`.

The CI source revision was merge commit `dd34e2728614dd5e85af33faab194d1c15e2dc8e` (PR head merged into its
then-base). The exact CI failure is the authoritative regression signal; it is not proof by itself that the
Pyramid upgrade caused the duplicate vulnerability. Establish that causal mechanism before changing code.

## Objective

Make the existing Pyramid 2.1 upgrade mergeable by identifying and repairing the concrete compatibility issue
behind this failed auto-upgrade. Keep the upgrade scope tight: prefer an existing compatibility pattern, minimal
integration/appsec adjustment, or correct lockfile constraint over broad tracer refactors.

## Required Investigation

1. Delegate focused subagents to independently inspect: the PR/lockfile diff and Pyramid 2.1 changes; the failed
   IAST test and its route; and the relevant Flask/Pyramid/AppSec instrumentation path. The main agent coordinates
   findings and rejects unsupported causal claims.
2. Reproduce the specific failure in the matching Riot environment if available. Capture the two vulnerability
   payloads, including source, location, and stack data, rather than only reporting `2 != 1`.
3. Compare the pre-upgrade and upgraded dependency graphs and request-processing behavior. Determine whether the
   duplicate originates in Pyramid, Flask/Werkzeug interaction, the IAST sink, or unrelated CI nondeterminism.
4. Implement only the proven compatibility fix. Preserve existing IAST detections and do not hide a genuine second
   vulnerability by weakening assertions or deduplicating output without a mechanism-based justification.
5. Use separate focused subagents for implementation, test-failure diagnosis, and final adversarial review.

## Scope and Constraints

- Likely relevant areas include the affected `.riot/requirements/*.txt` lockfiles, integration registry/version
  declarations, `ddtrace/contrib/internal/pyramid/`, Flask/AppSec/IAST code, and the named regression test. Do not
  edit all of them unless investigation proves each change necessary.
- Do not modify the portable control bundle as part of the product implementation commit.
- Do not add a blanket test skip, xfail, or broad duplicate-vulnerability suppression to make CI green.
- Preserve support for existing Pyramid versions and unaffected Flask/AppSec routes.
- Use `scripts/run-tests` with the relevant `--venv`; never invoke `pytest` directly.

## Acceptance Gates

1. Baseline evidence identifies whether the failure reproduces and records both observed vulnerability payloads.
2. The fixed matching environment returns exactly one `UNVALIDATED_REDIRECT` for the insecure-header route with
   the expected location/source evidence.
3. Focused regression tests and the applicable Riot/integration lane pass.
4. Final build, tests, lint, review, and observability gates run against one unchanged revision.
5. The final observability receipt includes inspectable trace or local test-agent evidence, not only test output.
6. If the matching container, Riot environment, or trace capture is unavailable, stop BLOCKED with the exact
   capability needed; do not substitute an unproven fix.
