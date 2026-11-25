# Codebase conventions

Repo-specific idioms: configuration, logging, shared helpers, test patterns, and build tooling. [AGENTS.md](../../../../AGENTS.md) also applies.

**When planning:** take the rules below into account when designing the change.

**When reviewing:** report a finding when the diff matches a rule's `Flag:` line and no `Not when:` applies, or when it has a clear problem in this lens's area that no rule covers (mark it `no rule`). Cite the rule ID and name the helper or pattern to use.

## 1. Release notes

### CONV-1.1: Write release notes as one or two customer-facing sentences
- Flag: a note that names internal mechanisms, root causes, components, metric tags, debug logs, or future plans, or runs past two sentences without a code example. See [releasenotes.rst](../../../../docs/releasenotes.rst) and the [releasenote](../../releasenote/SKILL.md) skill.
- ❌ `Fixes a race in the span aggregator's flush buffer, which also adds new metric tags and debug logs.`
- ✅ `Fixes an issue where spans could be dropped when the application exits during a flush.`

### CONV-1.2: Add a release note only for customer-visible changes
- Flag: a note for a test-only, CI-only, build-tooling, or debug-log-only change, or a `fixes` entry in a PR that doesn't fix the bug.

### CONV-1.3: Use one fragment per PR, one entry per fix, and the right section and scope
- Flag: several fragment files in one PR, two fixes in one entry, a performance improvement under `fixes` instead of `other`, or a scope prefix that isn't a real component (`Stats computation:` instead of `internal:`).

### CONV-1.4: State exactly what changed and how to enable it
- Flag: a note for an opt-in or default-changing feature that omits the default or the env var to set, says "the tracer" when only an integration changed, omits which library versions a fix applies to, or describes the trigger more narrowly than the code (for example, "ddtrace-run" when SSI is affected too).

## 2. Configuration

### CONV-2.1: Register new or renamed settings with `add-new-configuration`
- Flag: a new `DD_*`/`_DD_*`/`OTEL_*` variable with no matching entry in `supported-configurations.json`. See [add-new-configuration](../../add-new-configuration/SKILL.md).

## 3. Internal APIs

### CONV-3.1: Read span values in ddtrace internals with `span._get_attribute`
- Flag: `span.get_tag(...)`/`span.get_metric(...)` in `ddtrace/_trace/` or other internal code.
- ❌ `route = span.get_tag(http.ROUTE)`
- ✅ `route = span._get_attribute(http.ROUTE)`

## 4. Tests and test environments

### CONV-4.1: Test every version the PR claims to support
- Flag: a PR claiming support for a library major (for example, mcp 2.x) with no test venv locked to it or no matching max version in `scripts/integration_registry/registry.yaml`; or a suitespec Python list that adds `'3.15'` while still missing `'3.14'`.

### CONV-4.2: Use snapshot tests for integration spans
- Flag: new integration tests that assert span attributes one by one where a snapshot under `tests/snapshots/` would cover them.

## 5. Repository and CI

### CONV-5.1: Edit skills under `.agents/skills/`
- Flag: changes under `.claude/skills/`, which is a compatibility symlink to `.agents/skills/`. See [AGENTS.md](../../../../AGENTS.md).

### CONV-5.2: Schedule expensive benchmark jobs like the existing ones
- Flag: a new benchmark job that runs on every push or PR instead of following the macrobenchmark schedule rules, or explicit Kubernetes CPU/memory requests without `DD_DISABLE_VPA`.

### CONV-5.3: Add codespell false positives to `ignore-words-list`
- Flag: whole lines added to `.codespellignorelines` to silence one word, instead of adding the word to `ignore-words-list` in `setup.cfg`.

## Don't flag
- A release note with no PR number; it is encouraged but not required.
- Release notes in separate PRs that describe related changes to the same feature.
