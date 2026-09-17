Override for `reviewers/design.md` (in the core skill folder) — read that file first, then this.

# Design — dd-trace-py specifics

Open the doc that matches the change and check the diff against it. Do not restate those rules here.

- First, *where* the change belongs — product (`ddtrace/appsec`, `profiling`, …),
  integration (`ddtrace/contrib/`), or core (`ddtrace/` top-level and
  `ddtrace/internal/`). If the diff sits on a boundary or mixes those
  concerns, start here:
  [`docs/contributing-design.rst`](../../../docs/contributing-design.rst)
  § "Parts of the Library"
- A change in `ddtrace/contrib/` (or a new integration):
  [`docs/contributing-integrations.rst`](../../../docs/contributing-integrations.rst)
  § "What's an Integration?" and "What tools does an integration rely on?"
- A change that wires a security product (AppSec / IAST / AI Guard) to a shared integration:
  [`.cursor/rules/isolated-responsibility.mdc`](../../../.cursor/rules/isolated-responsibility.mdc)
