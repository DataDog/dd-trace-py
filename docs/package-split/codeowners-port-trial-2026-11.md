# Port trial report: `ddtrace.internal.codeowners` (Nov 2026)

First end-to-end exercise of the package-split workflow (skill + harness + gates)
against a minimal module, for the purpose of finding friction and inconsistencies
**before** real extractions start. The trial ran through skill steps 1 (inventory,
contract proposal, approval) and 2 (baseline gates, negative fixtures, harness port
to datadog-apm-python) and stopped at the step-2 stop rules: gates that pass
vacuously or fail structurally cannot anchor a freeze (plan: "Missing prerequisites
fail. Stop on unproven contracts.").

- Source branch: `gnufede-ddtracepy-split-guardrails` @ `92ca90c4e4`
- Destination branch: `gnufede-port-codeowners` @ `e453159e7` (worktree)
- Approved contract: `Codeowners.__init__(path=None, cwd=None)` + `.of(path)` only;
  module + its two 20-line test files; `ddtrace_internal_base` layer; native-free;
  stdlib-only. Approved by owner during the trial.

## Inventory results (step 1)

- Production (`api-usage --module ddtrace.internal.codeowners`, manifest source
  paths): 1 symbol (`Codeowners`), 3 consumers — `ci_visibility.recorder`
  (constructs, `.of()`, returns instance), `test_visibility.api` (annotation,
  `.of()`), `testing.internal.tracer_api` (re-export only).
- Tests (`--source-path tests/internal --source-path tests/testing`):
  2 files, constructor + `.of()` only. `tests/suitespec.yml` references the
  module but is YAML and invisible to the inventory.
- Indirect consumer found only by grep: `ddtrace.testing.internal.session_manager`
  imports `Codeowners` **via the tracer_api re-export** and fully uses it — see F6.

## Friction findings

Grouped: (A) inventory, (B) gates/harness, (C) destination port, (D) process/docs.

> **Disposition update (same day):** the inventory-side findings were fixed
> on `dd/gnufede/package-split-api-inventory-k7q2` (commit `65314c5610`, on
> top of the review-feedback commits `4e4b3b581d`/`fd0a21f8db` which had
> already fixed F1/F2). F8/F15 were fixed on this branch in `82736a9205` —
> with the per-package-manifest convention (F17), all applicable gates now
> run and pass for extracted packages. The first real extraction
> (ddtrace.internal.codeowners -> _ddtrace_internal_base.codeowners,
> bridge `04d4480bc6`, freeze `5e3b668550`) completed end-to-end; its new
> findings are F19/F20 below. Remaining open: F7 (root-manifest red
> baseline, transitional), F14 (stdlib enumeration), F17 docs, F5/F9.

### A. Inventory

- **F1 — Interpreter version unpinned.** The wrapper is a uv script with
  `requires-python >= 3.9`; uv resolves any interpreter. `ast.parse` uses that
  interpreter's grammar, so PEP 750 template strings in
  `tests/appsec/iast/aspects/test_template_string_aspect.py` (3.14-only) abort the
  whole tests inventory with a bare `invalid syntax` and no version hint.
  Workaround: `UV_PYTHON=3.14`. **Fixed** on the api-inventory branch
  (review-feedback commits): unparsable files are skipped, reported in
  evidence and warned on stderr; a sub-3.14 interpreter now yields a loud
  partial scan instead of an abort.
- **F2 — One unreadable file kills the scan.** Deliberately non-UTF-8 fixtures
  (`tests/appsec/iast/fixtures/{aspects,ast}/str/non_utf8_content.py`) crash
  `read_text(encoding="utf-8")`; the gate aborts instead of skipping/recording.
  14 non-UTF-8 files exist under `tests/`. **Fixed** on the api-inventory
  branch: `parse_source` honors PEP 263 encodings and records the rest as
  unparsable.
- **F3 — Evidence clobbering.** "production, then tests" both write
  `<output>/api-usage/api-usage.json`; the second run overwrites the first. No
  scope tag in evidence paths. Workaround: `--output` per scope (which then hits
  F16).
  **Fixed** on the api-inventory branch: evidence lives under a per-scope
  subdirectory and records its source_paths as provenance.
- **F4 — Tests not in manifest scope.** `source_paths = ["ddtrace"]` only; nothing
  documents how to inventory tests, and the `--source-path` help reads as if it is
  ignored when a manifest exists (it actually overrides — good, but say so).
  **Mostly addressed** by the api-inventory branch: tests scans work, are
  scoped separately, and `--allow-absent-target` (F18) covers consumer-only
  scopes; the manifest wording is a guardrails-side doc fix.
- **F6 — Re-export indirection is invisible.** Attribution is direct-import only;
  `session_manager.py` (full usage of `Codeowners`) does not appear in the
  used-API surface because it imports through `tracer_api`. Every inventory
  needs a manual re-export grep until fixed. Pinned by
  `test_reexport_consumers_are_not_attributed`.
  **Fixed** on the api-inventory branch (`65314c5610`): from-imports and
  module-rooted attribute chains resolve transitively through each module's
  import-binding map; the codeowners production inventory now reports 4
  consumers including `ddtrace.testing.internal.session_manager`.

### B. Gates / harness

- **F7 — Pre-existing red baselines are undocumented.** On the untouched tree,
  `boundaries` fails (11 SCCs, 16 dynamic-import violations — known monolith
  reality) and there is no mechanism to snapshot known pre-existing failures, so
  "never weaken baselines" has nothing to compare against. Decide: which gate
  results constitute the recorded baseline for the freeze.
- **F8 — `lifecycle/import-clean` is structurally unrunnable.** It imports by
  dotted name inside the harness's own bare uv env (packaging + tomli only):
  `ddtrace` → `ModuleNotFoundError: envier`. Never installs from source paths or
  adds them to sys.path. The failure reads like a code failure but is
  environmental.
  **Fixed** in `82736a9205`: the probe's sys.path now uses the directory that
  actually contains a declared import root (the source path itself for
  src-layout/per-package manifests, its parent for namespace layouts). The
  codeowners package lifecycle gate passes; the *root* manifest (native
  ddtrace, envier, compiled extensions) remains unrunnable there, which is
  expected while gates run per extracted package.
- **F15 — uv workspace virtual root breaks `artifacts` (false FAIL).** Every
  artifacts sub-check passes, but fresh-install also installs
  `datadog-apm-python-workspace` (virtual, no RECORD) and the fail-closed
  RECORD verification raises → gate exits 1 with all checks green.
  **Fixed** in `82736a9205`: RECORD verification fails closed only for the
  manifest's own distribution; other RECORD-less distributions (virtual
  workspace roots) are skipped. The codeowners artifacts gate now exits 0.
- **F12 — Module naming breaks on workspace layouts (vacuous PASS).** Names are
  derived relative to the manifest root, stripping only a *top-level* `src/`
  segment. `packages/<dist>/src/<module>` yields dotted names like
  `packages.ddtrace-internal.src._ddtrace_internal` that never match
  `import_roots`, so `internal_edges` is empty and import-cycles/edge-policy
  pass while evaluating nothing. A repo-root manifest on datadog-apm-python
  produced a green `boundaries` that had checked zero edges. Pinned by
  `test_workspace_src_layout_gates_pass_vacuously` (with a per-package-manifest
  control half that correctly fails).
  **Fixed for inventories** on the api-inventory branch (`module_names_for`
  names after the last `src` segment); the duplicated copy in
  `astdeps._module_names_for` (gates) is still open here and should be deduped
  into one shared helper when this branch rebases onto the merged inventory PR.
- **F18 (new, surfaced by the fixes) — consumer-only scopes failed closed.**
  Scanning `--source-path tests` alone errored ("no scanned module matches")
  because the target lives outside the scope. **Fixed** on the api-inventory
  branch with `--allow-absent-target` (opt-in) plus a better error message.

### E. First real extraction (ddtrace.internal.codeowners)

- **F19 — approved rewrites authorize by path only, so the freeze gate stops
  protecting a rewritten path.** The codeowners freeze manifest records the
  bridge as an approved rewrite; after that, *any* further change to
  `ddtrace/internal/codeowners.py` (committed or not) passes the freeze gate
  -- verified empirically with a committed drift that exited 0. The schema
  needs per-rewrite post-rewrite hashes (an approved new baseline), or
  rewrites must expire. Until then, an approved-rewritten path has no ongoing
  freeze protection.
- **F20 — freeze drift detection only sees committed revisions.** The drift
  check is `git diff <extraction_revision>..<head>`; uncommitted working-tree
  changes to frozen paths are invisible to a local gate run. CI heads are
  always committed, but local runs give false confidence.
- Recorded for the record: the destination's stricter ruff/pyright baseline
  required scoping the vendored harness out of ruff (`ruff.toml` exclude +
  `force-exclude`) and small typing adaptations of the ported module
  (`re.Pattern[str]`, a dead `is None` check, module docstring) -- the
  "verbatim port then lint locally" workflow needs a documented policy for
  harness copies.
- **F21 — pre-commit `--all-files` skips untracked files, so local lint
  under-reported until commit time.** The destination package's 13 ruff
  findings (complexity, pathlib, loop-variable, docstring-format rules)
  passed the local `scripts/lint check` because the package files were
  still untracked when the check ran; CI failed on the exact same tree.
  Local rule: commit (or at least stage) new files before the final lint
  run, and run per-tool commands directly on untracked paths. Lesson for
  future extractions: budget a lint-adaptation pass beyond the verbatim
  copy -- a behavior-identical port can still fail a stricter baseline.
  (Post-fix: behavior verified identical by a differential corpus test
  against the pre-extraction implementation; the Datadog code-quality
  warnings on the vendored harness (astdeps.py nested-blocks,
  no-range-loop-with-len) are now ignored in static-analysis.datadog.yml,
  consistent with the ruff exclusion.)
- **F14 — No stdlib exemption in edge-policy.** `typing` and `__future__` count
  as undeclared external dependencies; a stdlib-only package (exactly the
  `ddtrace_internal_base` rule) must enumerate every stdlib module in
  `external`, conflating stdlib imports with packaged dependencies and inviting
  wildcard openings. Recommend `sys.stdlib_module_names` exemption.
- **F15 — uv workspace virtual root breaks `artifacts` (false FAIL).** Every
  artifacts sub-check passes, but fresh-install also installs
  `datadog-apm-python-workspace` (virtual, no RECORD) and the fail-closed
  RECORD verification raises → gate exits 1 with all checks green. Recommend
  restricting verification to the target distribution.

### C. Destination port

- **F9 — Port instructions incomplete.** The wrapper says "copy
  scripts/package_split next to your own wrapper", but the wrapper also needs
  `scripts/uv-run-script` + `scripts/_uv-bootstrap.sh`. Harmless once known.
- **F11 — Manifest schema can't express the destination's reality.**
  `destination_repository` is required *in the destination repo itself*;
  `supported_exports` cannot be empty (nothing pinned yet); `release.resources`
  must not be empty (forced adding a genuinely-correct `py.typed`, but the
  schema should allow "no resources"); `[[dependencies.allowed_edges]]` list
  syntax and `[contracts]` shapes are only discoverable by error round-trips.
- **F13 — Root manifest on a uv workspace builds the wrong thing.** With a
  repo-root manifest, `artifacts` builds the workspace *virtual root* (0-file
  wheel) and passes sub-checks vacuously (`sdist-rebuild ... 0 files`). The
  per-package manifest placement fixes it — see F17.
- **F17 (dissolves most of the above) — Per-package manifest convention is
  implicit.** The intended usage (manifest inside `packages/<name>/`,
  `source_paths=["src"]`) is only hinted at by a comment in dd-trace-py's root
  manifest ("gates run per extracted package, not over the whole tree at once").
  Document it in the README/skill; with it, naming (F12), build target (F13) and
  fresh-install behave correctly — boundaries PASS with real names, artifacts
  builds and installs `ddtrace_internal-0.2.0` (only F15 remains).

### D. Process / tooling

- **F5 — Skill discovery vs branch checkout.** The skill ships on the branch;
  pi's skill discovery runs at session start, so after `git checkout` the skill
  is absent until restart; `agent-deck skill attach` refuses ("target already
  exists and is not managed"). Manual SKILL.md read works.
- **F10 — `api-usage` on `tests/` as a whole is impossible today** (F1+F2), so
  the skill's "production, then tests" step has no sanctioned scope for tests.
- **F16 — Custom `--output` dirs escape `.gitignore`.** Only `.package-split/`
  is ignored; per-scope outputs (F3 workaround) show up in `git status`.
  **Fixed** on the api-inventory branch (pattern widened to `.package-split*/`);
  the guardrails branch needs the same line when it rebases.

## Commands and evidence

- `UV_PYTHON=3.14 ./scripts/package-split --output .package-split-codeowners api-usage --module ddtrace.internal.codeowners`
- `... api-usage --module ddtrace.internal.codeowners --source-path tests/internal --source-path tests/testing`
- `... api-surface --top 15`
- `... boundaries` / `... lifecycle` / `... artifacts` (both repos; destination
  with `--manifest packages/ddtrace-internal/package-contract.toml`)
- Test suite: `scripts/run-tests --venv ebecc59f90da` → 156 passed.
- Lint: `scripts/lint fmt` on the touched test files → clean.

Evidence JSONs were produced under `.package-split*` (not committed; gitignore gap
F16) and summarized above.

## Status, blockers, next steps

- **Done:** inventory (with re-export attribution), narrow contract approved,
  harness ported to destination, per-package seed manifest, vacuous-green
  boundaries fixture committed, seed `py.typed`, inventory-side fixes
  F1/F2/F3/F6/F12/F16/F18 on the api-inventory PR, gate fixes F8/F15
  (`82736a9205`), and the **first end-to-end extraction**: destination package
  `ddtrace-internal-base` (datadog-apm-python `1557e84bc`, gates boundaries /
  lifecycle / artifacts all PASS on the per-package manifest), dd-trace-py
  bridge (`04d4480bc6`, behavior-identical, provider-first, broken-provider
  loud, pinned by tests), freeze manifest (`5e3b668550`, freeze gate PASS).
- **Blocked before the next extraction:** F19 (freeze rewrite semantics — the
  core protection gap) and the F14 stdlib-allowlist decision. F7 remains a
  transitional non-blocker for per-package gates.
- **Compatibility/freeze:** nothing published, no runtime dependency added;
  the cutover (flip dd-trace-py to a hard ddtrace-internal-base dependency,
  drop the vendored fallback, release rehearsal) is gated on review and the
  F19 fix. Rollback: revert the bridge and freeze commits on dd-trace-py and
  the package commit on datadog-apm-python; consumers never changed.
- **Recommended order:** merge the api-inventory PR; rebase guardrails onto
  it (take its inventory files wholesale, unify `module_names_for`); fix F19
  (per-rewrite post hashes) and F14 (stdlib allowlist); then run the next
  extraction through this same pipeline.
