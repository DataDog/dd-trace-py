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
> already fixed F1/F2). Remaining open findings are the gate/harness ones:
> F7, F8, F14, F15, the astdeps half of F12, F17, and process notes F5/F9.

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
  environmental. Needs an env story (install artifact first / run in package
  venv) or an explicit skip with reason.
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
  boundaries fixture committed (guardrails `2e8f85579f`), seed `py.typed`,
  destination commit `e453159e7`, inventory-side fixes F1/F2/F3/F6/F12/F16/F18
  on the api-inventory PR (`65314c5610`).
- **Blocked before extraction (step 3):** F8 (import-clean unrunnable), F12
  astdeps half (naming — fixed in practice by per-package manifests, needs the
  rebase dedupe), F15 (artifacts false-fail), F7 (baseline snapshot decision).
- **Compatibility/freeze:** nothing extracted, nothing published; freeze gate
  not exercised (no baseline rev yet). Rollback = drop the local commits on both
  branches; the harness port is additive-only.
- **Recommended order:** merge the api-inventory PR, rebase guardrails onto it
  (take its `api_usage`/`api_surface`/`cli`/tests wholesale; drop the duplicated
  copies here; unify `module_names_for`), then fix F12-astdeps/F14/F15/F8 on
  guardrails, and re-run this trial as a smoke test before the first real module.
