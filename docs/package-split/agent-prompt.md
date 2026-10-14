# AI agent prompt: guarded package extraction

Use this prompt with [the migration plan](plan.md). Replace the inputs before execution.
The plan's tooling now exists: `scripts/package-split` (thin wrapper) over the
versioned harness in `scripts/package_split/`; see `scripts/package_split/README.md`.
Remaining names and scope decisions are still open and require approval.

---

You are migrating a bounded part of dd-trace-py into independently released packages.
Follow AGENTS.md, applicable domain guides and the supplied migration plan.

## Inputs

- Target package/capability: <target>
- Source repository and revision: <source>
- Namespace extraction POC and revision: <POC or not supplied>
- Destination repository and approved import/distribution names: <destination or undecided>
- Approved scope and current phase: <scope>
- Supported versions/platforms and optional features: <policy or undecided>

Do not invent missing decisions. You may inventory and propose changes while decisions are pending,
but obtain approval before committing to package boundaries, native replacements, compatibility
windows or behavior changes. Do not assume an unreviewed POC establishes the final architecture.

## Non-negotiable outcomes

1. Products release without rebuilding unrelated native components. Compatible shared-package releases
   do not require product releases. Each package builds, tests and releases from its own checkout.
2. The default Test Optimization runtime dependency graph is native-free, excluding the interpreter
   and standard library. Native-dependent features require explicitly approved optional adapters;
   they must not enter or auto-activate in the default installation.
3. Shared Python contents may be broad; supported APIs must remain narrow and explicit.
4. Supported packages coexist with canonical shared state and single instrumentation activation.
5. Existing supported ddtrace behavior remains intact. Legacy pytest v2 and IAST pytest removal is
   separate ongoing work: coordinate with it, but do not restore it or silently broaden this task.
6. New package imports do not activate products, patch application behavior, start threads, configure
   application logging or perform network/process operations. Preserve intentional legacy startup
   through the ddtrace compatibility bootstrap.

## Work in evidence-backed phases

### A. Inventory and propose

- Inspect checkout status and local instructions; preserve unrelated changes.
- Review the supplied POC. Map static, dynamic, transitive, build and resource dependencies, including
  parent-package initialization and process-wide state. Distinguish findings from assumptions.
- Propose a package contract manifest, acyclic dependency graph, narrow supported API, lifecycle
  ownership, optional adapters and a small first implementation slice.
- Report blocking decisions and obtain approval before expanding the approved scope.

### B. Build the safety net before moving code

- Inventory existing tooling. Implement missing gates from the plan incrementally, with negative
  fixtures proving that violations fail. Reuse import-analysis and existing test/lint infrastructure.
- Add behavior tests alongside API snapshots; do not claim signature comparison proves compatibility.
- Establish standalone artifact tests, import/lifecycle probes and supported version/migration matrices.
- Keep unit/contract tests in the owning package and cross-product end-to-end tests in dd-trace-py.
  Central tests must consume exact candidate artifacts, not require coordinated source changes/releases.

### C. Extract incrementally

- Use approved namespaces outside ddtrace. Do not introduce package dependency cycles or conceal
  structural problems with deferred imports. Apply the circular-import and dependency-direction skills.
- If temporary dual imports are necessary, fall back only for a genuinely absent new top-level package.
  Propagate failures inside installed providers and missing symbols/submodules. Test absent, installed,
  broken and coexistence cases; preserve canonical object identity and record a removal condition.
- Build from the standalone checkout and source distribution, then test installed wheels outside source
  trees. No editable installs, undeclared sibling paths or inherited native build requirements.
- Freeze only after boundary validation. Pin extraction provenance; verify destination parity against
  approved rewrites. Never weaken freeze rules or silently update compatibility baselines to pass CI.

### D. Verify and hand over

- Run applicable boundary, contract, artifact, lifecycle, version-skew, migration and feature gates.
  Use scripts/run-tests and scripts/lint as required by this repository; never invoke pytest or raw
  lint tools directly. Benchmark performance-sensitive/native changes using the applicable skill.
- Confirm imports are inert, activation is idempotent, default Test Optimization is native-free,
  coexistence is correct and supported upgrades/rollback preserve behavior.
- Demonstrate independent release readiness using tested artifacts. Do not publish, push, delete source,
  change credentials or perform irreversible cutover without explicit authorization.
- Do not invoke subagents unless delegation is authorized.

## Final report

Provide only:

- Changed files and the boundary established.
- Exact validation commands, outcomes and evidence/artifact paths, including revisions and hashes.
- Unexecuted checks, blockers and residual risks; never report an unrun gate as passing.
- Compatibility/freeze status, rollback path and the next approval needed.

Stop before cutover if required contracts fail, dependency closure remains unknown, native code enters
Test Optimization's default graph, shared state is duplicated or source/destination parity is unproven.
Do not trade away these guardrails to make extraction appear complete.
