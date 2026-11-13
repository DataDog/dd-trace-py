# Package split harness audit

Keep the consumer inventories and source freeze tooling in dd-trace-py. Run
import-inertness checks against extracted packages in their own environments.
Use concrete consumer and bridge tests to establish compatibility during each
extraction. The current generic gates should not all become prerequisites for
moving a module.

This assessment covers `gnufede-ddtracepy-split-guardrails` at `f9cda8db85`.
[datadog-apm-python PR 59](https://github.com/DataDog/datadog-apm-python/pull/59)
provides the starting question: which checks add coverage beyond a repository's
existing build, import and test workflows? Its destination-side decision to keep
only `lifecycle` does not transfer wholesale to the source repository.

## Coverage and proposed ownership

| Component | Use in dd-trace-py | Disposition |
| --- | --- | --- |
| `api-usage` | Identifies the symbols and consumers an extraction must preserve. | Keep as an inventory; supplement with searches for re-exports, dynamic imports and configuration references. |
| `api-surface` | Helps distinguish shared utilities from modules that belong with one product. | Keep as an exploratory inventory; bundling suggestions do not establish package boundaries. |
| `freeze` and its PR workflow | Detects source drift while the extracted copy is developed elsewhere. | Keep, fix approved rewrite enforcement, and activate for the actual extraction scope. |
| `lifecycle` import checks | Detects import-started threads, logging changes and other process side effects. | Keep for extracted packages, preferably owned by the destination. Legacy ddtrace startup needs its own behavioral contract. |
| `lifecycle` activation checks | Exercises a declared activate/deactivate API and observed registries. | Retain only for packages that expose such an API; use product tests for actual activation and fork behavior. |
| `boundaries` cycles | Finds cycles, but the root invocation rejects existing monolith cycles. | Reuse the existing cycle regression job for ddtrace. A new extracted package can require zero cycles in its destination. |
| `boundaries` edge and dynamic-import policy | Can enforce a small package's dependency boundary. The root policy permits every internal edge and external import. | Prefer destination import contracts and dependency checks. Keep targeted coverage for dynamic forbidden imports where the replacement does not cover them. |
| `boundaries` build metadata | Compares dependency names with a second declaration in the contract. | Remove the duplicate dependency inventory when its useful policy is enforced against pyproject and built metadata directly. |
| `artifacts` build/install/import checks | Overlaps the wheel pipeline's fresh environment and smoke tests. | Reuse the wheel pipeline; add any missing extracted import/resource check there or in the destination. |
| `artifacts` sdist rebuild | Adds a source-distribution check beyond the inspected wheel smoke path. | Preserve the requirement where needed, but compare appropriate package contents; byte equality of rebuilt native binaries is too broad a default. |
| `artifacts` native checks | Useful for a package or dependency closure promised to be native-free. | Run at that boundary. The native-required ddtrace distribution cannot prove a native-free Test Optimization dependency closure. |
| `contracts` | Can catch a changed supported function signature against a released wheel. | Defer a generic gate until there is a concrete supported API and baseline. Run consumer-owned behavior tests for the extracted API. |
| `compatibility` | Installs candidate/released combinations and runs explicit expectations. | Keep the scenario requirement; prove value with an actual extraction matrix before maintaining a separate runner indefinitely. |
| `migration` | Exercises installation/removal, ownership, bridge resolution and entry-point metadata. | Keep extraction-specific scenarios; strengthen identity and effective instrumentation assertions before treating it as a migration guarantee. |
| Manifest, evidence, venv and wheel helpers | Support the commands above. Evidence distinguishes failed, skipped and required unrun checks. | Keep helpers used by retained commands; trim them after ownership is settled. Keep review decisions without duplicating package metadata. |

## Existing checks and differences from the destination

The source repository already runs
[`detect_circular_imports` and `detect_layering_violations`](../../.gitlab-ci.yml),
using [`scripts/import-analysis`](../../scripts/import-analysis/). Both compare
against the merge base. This is a useful monolith regression policy, whereas
`boundaries` demands an entirely cycle-free graph on every invocation. The two
policies answer different questions.

The layer rules describe architectural zones inside ddtrace. They do not replace
an extracted package's external dependency declaration, its prohibition on
importing ddtrace, or every dynamic-import check. This branch has no deptry or
import-linter wiring. Removing those checks here requires either establishing
their replacement at the destination or explicitly leaving that coverage open.

The wheel pipeline's
[`test_wheel()`](../../.gitlab/scripts/build-wheel-helpers.sh) installs the final
wheel into a fresh environment and runs [`tests/smoke_test.py`](../../tests/smoke_test.py)
from outside the checkout. This covers substantial build/install/import behavior.
It does not establish all supported exports, resources, console scripts, or
sdist-to-wheel parity. Extend those existing checks where there is a concrete
requirement instead of maintaining a second general build pipeline.

On this branch the only package-split gate invoked by a dedicated CI workflow is
[`freeze`](../../.github/workflows/package-split-freeze-guard.yml). The
[`package_split` test suite](../../tests/suitespec.yml) tests harness fixtures;
passing it is not evidence that the real package passed all seven gates.
There is no committed `package-freeze.toml` at the audited revision, so the
workflow has no active freeze scope there.

## Gaps that affect the decision

1. **Approved rewrites are approved by path, not content.**
   [`freeze._verify_freeze`](../../scripts/package_split/gates/freeze.py) builds a
   set of approved paths and exempts every diff on those paths. It never checks
   the approved head content against `post_hash`, despite that requirement in
   the plan. The existing approved-rewrite fixture passes with this broader
   allowance. Enforce exact approved content and reject a subsequent edit before
   relying on the guard for a live extraction.

2. **Migration origin is weaker than canonical identity.**
   [`probe._identity_probe`](../../scripts/package_split/probe.py) reports
   `__module__`; the migration gate compares that string with `expect_module`.
   It does not compare the bridge export with the provider export using `is`.
   Entry-point counting also measures installed metadata, not effective
   instrumentation. Add assertions against the actual shared class/state and
   observable product behavior. Scenarios check their final state, so use
   separate scenarios or explicit checks for important intermediate states.

3. **The default contracts gate covers almost no API.**
   The root manifest pins only `ddtrace.__version__` and no behavioral fixtures.
   The snapshot compares its type, not its value. It does not inspect the methods
   of an exported class, either. A green result is insufficient to establish
   compatibility of an extracted class such as Codeowners and its `of()` method.

4. **Configuration records are not executable coverage.**
   `dependency_ranges`, `consumer_contract_suites` and
   `supported_version_combinations` are not used to generate or execute gate
   scenarios. Python/platform support fields are not a test runner matrix.
   Require concrete commands and artifacts for the combinations that matter;
   keep descriptive ownership and rollout records clearly separate.

5. **Some green fixtures deliberately preserve known limitations.**
   `test_workspace_src_layout_gates_pass_vacuously` records a real boundaries
   false pass: a workspace root manifest can misname all modules and check no
   relevant edges. The matching per-package manifest fails correctly. Use a
   manifest next to the package pyproject and require a nonempty discovered
   scope if retaining this gate. The source inventory on this revision also
   needs the fixes described in the
   [codeowners trial](codeowners-port-trial-2026-11.md), including re-export
   attribution and robust parsing of test sources.

The default root `lifecycle` invocation fails on a missing `envier` dependency
in the wrapper's environment, reproducing the earlier trial. This is not proof
of an import-side-effect violation. Run the probe with the extracted package
and its resolved dependencies installed.
Importing ddtrace itself has existing startup behavior, so universal inertness
is not an appropriate acceptance test for the monolith.

## Proposed sequence

1. Keep inventories and fix their known scan gaps. Inventory production and test
   consumers separately, then review a narrow API and dependency boundary.
2. Fix freeze approval hashes and add the actual extraction freeze on the base
   branch before copying code. Retain the workflow's protection against trusting
   rules or executable code from the PR head.
3. Make the destination responsible for its build, dependency policy, import
   inertness and supported API. Cite results for exact candidate artifacts.
4. In dd-trace-py, exercise the compatibility bridge with candidate/released
   combinations, missing and broken providers, shared identity, install order
   and rollback. Reuse scripts/run-tests and consumer suites. Keep a generic
   matrix runner only if it makes these concrete scenarios easier to maintain.
5. Remove duplicated generic checks and unused helpers after the replacement
   coverage is visible. Preserve explicit gaps such as sdist parity and dynamic
   imports rather than assuming that an existing tool checks them.

## Validation

Environment discovery through `scripts/run-tests --list` selected the
`package_split` Python 3.14 environment `ebecc59f90da`. With Colima running:

- Focused inventory, boundaries, lifecycle, freeze and evidence selection:
  **55 passed**, seed `3761752157`.
- Complete `scripts/run-tests --venv ebecc59f90da`: **155 passed**, seed
  `3079254381`, including the real tiny-package build and crafted-wheel
  compatibility/migration checks. Both runs warned about the existing
  unrecognized `asyncio_mode` pytest option.
- `scripts/ddtest scripts/lint checks`: passed, with existing warnings.
- Targeted `scripts/lint spelling` for this assessment and the CLI README:
  passed.

Real source commands used the wrapper's isolated dependency environment inside
Colima: `scripts/ddtest uv run --python 3.14 --script scripts/package-split
--output .package-split/audit <command>`.

| Command | Result at the audited revision |
| --- | --- |
| `api-usage --module ddtrace.internal.codeowners` | One symbol across three consumer files. The re-export consumer in `ddtrace.testing.internal.session_manager` is absent. |
| `boundaries` | Fails on 11 strongly connected components and 16 dynamic-import problems; the permissive edge policy passes all 18,008 edges. |
| `lifecycle` | Fails `import-clean` with `ModuleNotFoundError: envier`; activation is explicitly skipped. |
| `freeze --base HEAD` | Fails with a required `not_run`: no protected freeze manifest exists. |

The generated JSON evidence is under `.package-split/audit/` and is ignored by
Git. These commands establish the default invocation's scope and limitations;
their failures are not test-suite failures.

These results validate the current fixture behavior. The freeze hash and
canonical identity gaps above are source-inspection findings, not newly added
regression tests. The worktree contains an assessment; it does not remove gates
or change runtime behavior.
