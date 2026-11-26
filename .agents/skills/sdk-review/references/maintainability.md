# Maintainability

Duplication, dead code, comments, naming, and test value.

**When planning:** take the rules below into account when designing the change.

**When reviewing:** report a finding when the diff matches a rule's `Flag:` line and no `Not when:` applies, or when it has a clear problem in this lens's area that no rule covers (mark it `no rule`). Cite the rule ID and point to the existing helper or simpler shape.

## 1. Comments and docstrings

### MAINT-1.1: Keep comments and docstrings short
- Flag: a multi-line comment or docstring paragraph that restates the code, narrates the reasoning behind a routine call, describes callers, or reads as generated text. See [AGENTS.md](../../../../AGENTS.md#docstrings-and-comments).
- ❌ a five-line comment above `tracer.configure(...)` explaining billing and `apm_opt_out`
- ✅ no comment, or one line stating the non-obvious invariant

### MAINT-1.2: Describe the current code, not its history or plans
- Flag: a comment, docstring, or README that refers to earlier or never-merged versions, specific commits or PRs, a "current status", or a rationale that only holds until a planned migration.
- ❌ `# Previously this scanned in parallel; now it runs on one thread.`
- ✅ `# Prefer slot 3, fall back to 4; never claim 1 (reserved by the runtime).`

### MAINT-1.3: Comment invariants that aren't visible at the call site
- Flag: a relaxed `mypy.ini` section, a dispatch whose order relative to `super()` matters, a guard against dispatching twice, a fork or deadlock workaround, or a generated file format, with no comment saying why.

### MAINT-1.4: Keep TODO conditions accurate
- Flag: a TODO whose stated removal condition is wrong, so the code stays valid after that event. Update a TODO rather than deleting it; see [AGENTS.md](../../../../AGENTS.md#development-constraints). Unmarked stubs are covered by COR-2.2.

### MAINT-1.5: Preserve existing rST structure when editing a docstring
- Flag: a docstring edit that drops `.. warning::` or other directives, or reformats content that didn't change.

## 2. Simplicity and dead code

### MAINT-2.1: Remove indirection that adds nothing
- Flag: a constant that just names a literal (`PY_315_VERSION_INFO = (3, 15)`), `*args, **kwargs` no caller passes, a Python-visible getter nothing reads, a re-export that exists only for tests, a standalone constant used once in a tuple, or `reversed()` where order doesn't matter.

### MAINT-2.2: Route edge inputs through the main code path
- Flag: an early return for an empty input that duplicates what the general path would do (for example, empty sampling rules skipping the shared sorted assignment).

### MAINT-2.3: Name things for what they do, and underscore private module state
- Flag: a job, function, or variable whose name doesn't match its behaviour (for example, a "build" job that only extracts binaries), or new module-level state without a leading underscore.

### MAINT-2.4: Type stubs and return values precisely
- Flag: `Any` in `ddtrace/internal/native/_native.pyi` where the concrete type is known, or a variable-length `tuple` return where the function always returns a fixed number of parts.
- ❌ `def context(self) -> Any: ...`
- ✅ `def context(self) -> Context: ...`

## 3. Tests

### MAINT-3.1: Add tests for each new helper and code path
- Flag: a new helper, class (for example, a `Thread` subclass with run-once semantics), workaround, or connection path with no unit or snapshot test, or tests that cover only one of several new helpers in a module.

### MAINT-3.2: Cover the states the change must not disturb
- Flag: identity, propagation, or lineage code tested only from a clean state (for example, no test where root and parent session IDs are already set in a child process).

### MAINT-3.3: Justify removed tests
- Flag: a deleted or weakened test with no reason in the PR description or commit message.

## Don't flag
- Missing annotations on private hook or callback parameters, especially where the annotation would import a type across a layering boundary.
- Deeply nested loops in one-off `scripts/` tooling.
