# Security & Safety

Secrets and sensitive configuration, CI and supply chain, PII, and safety inside customer processes.

**When planning:** take the rules below into account when designing the change.

**When reviewing:** also inspect commit messages and the PR description. Report a finding when the diff matches a rule's `Flag:` line, or when it has a clear security or safety problem that no rule covers (mark it `no rule`), with the leak path (source to sink).

## 1. Public repository hygiene

### SEC-1.1: Keep internal context out of commits, PR text, and release notes
- Flag: internal discussions, other teams' tickets, backend or data-pipeline names, internal URLs, or open internal questions in a commit message, PR description, release note, or comment. The PR's own Jira ticket is fine. See [AGENTS.md](../../../../AGENTS.md#pull-requests).

### SEC-1.2: Never commit secrets
- Flag: an API key, token, `DD-API-KEY`/`Authorization` header value, or credentialed URL in code, tests, fixtures, CI config, or a committed `.env` file.

## 2. CI and supply chain

### SEC-2.1: Make CI downloads fail loudly and retry, and never pipe them into a shell
- Flag: `curl` without `--fail` and transient retries, or a download piped into `sh`/`tar`. See [AGENTS.md](../../../../AGENTS.md#ci-scripts).
- ❌ `curl -sL https://example.com/install.sh | sh`
- ✅ `curl --fail --retry 5 --retry-delay 2 --retry-connrefused -sSL -o install.sh https://example.com/install.sh && sh install.sh`

### SEC-2.2: Use `gh_retry` and an exported `GH_TOKEN` for `gh` in GitLab CI
- Flag: a bare `gh` call, or `gh auth login`, in GitLab CI scripts outside a polling loop that already retries.

### SEC-2.3: Enforce required checks centrally, not with lists a PR can edit
- Flag: a merge-gating rule (for example, a frozen-teams list) hard-coded in a workflow file that the PR under review could edit or make non-required.

## 3. Customer-process safety

### SEC-3.1: Don't change how the application sees errors
- Flag: integration or product code that catches an application exception without re-raising it, or a wrapper that silences exceptions raised by the application's own log handlers. See [contributing-integrations.rst](../../../../docs/contributing-integrations.rst).
- Not when: the swallowed exception comes from ddtrace's own code, so the application never sees it.
