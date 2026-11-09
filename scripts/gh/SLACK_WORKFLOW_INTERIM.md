# Interim: structured Workflow Builder webhook

**Status:** interim only. Prefer a shared Incoming Webhook (`hooks.slack.com/services/...`) in `scripts/gh/.env.local` as `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL` (see `.env.local.example`). Freeform mrkdwn in a single Workflow `text` variable shows as literals; do not use that.

When `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL` is a `hooks.slack.com/triggers/...` URL, `create_gh_release.py` POSTs **flat string fields** (not `{"text": "<mrkdwn>"}`).

**API note:** Slack Workflow Builder definitions are **not** editable via GitHub/`gh`. Rebuild is a human click path in Slack (below), or switch `.env.local` to a `services/` Incoming Webhook URL.

## Live channel vs script (2026-11-09 wet 4.16.4)

| | What landed in `#apm-python-release` | What the script POSTed |
| --- | --- | --- |
| Mentions | hardcoded `@brettlangdon @emmett` | no `reviewer_*` keys; `releasers_handle=dd-trace-py-releasers` |
| Emoji | `:+1:` | template must use `:lgtm:` (script mock + Incoming Webhook do) |
| Footer | `This run was initiated by vlad-scherbich.` | `runner` + `script` / `script_branch` / `script_branch_url` / `commands_hint` → **Run by** … **Try \`patch\` / \`rc\` / \`final\`** |

**Root cause:** the Workflow **Send a message** step still has the old static template. The script payload is correct; rebuilding the Workflow (or switching to `services/`) is required. **Do not wet-POST again until that rebuild is done.**

## Payload keys (all strings) — webhook variable names must match exactly

| Key | Example | Workflow variable type |
| --- | --- | --- |
| `version` | `4.16.4` | Text |
| `kind` | `patch release` | Text |
| `draft_url` | `https://github.com/.../releases/tag/untagged-…` | Text |
| `target_sha` | `593e9eaa255a` | Text |
| `releasers_handle` | `dd-trace-py-releasers` | Text (optional; prefer static User Group below) |
| `runner` | `vlad-scherbich` | Text (GitHub login) |
| `channel_id` | `C04MK6NNDG9` | Channel ID (optional) |
| `script` | `scripts/gh/create_gh_release.py` | Text |
| `script_branch` | `vlad/release-create-release-rc-script` | Text |
| `script_branch_url` | `https://github.com/DataDog/dd-trace-py/compare/main...vlad/release-create-release-rc-script` | Text |
| `commands_hint` | `` `patch` / `rc` / `final` `` | Text |

**Delete from the Workflow if still present:** `reviewer_brett`, `reviewer_emmett`, freeform `text` mrkdwn blob, any static `@brettlangdon` / `@emmett`, any `:+1:`, any `This run was initiated by`.

## Exact Send-a-message body (paste / rebuild)

Build this in the Workflow editor. **Bold** / *italic* via the editor. **Insert variable** where noted. For the ping: Insert → **User Group** → `@dd-trace-py-releasers` (do **not** insert `releasers_handle` as plain text — that does not notify).

```
:package: *Draft* {kind} {version} is ready for review

{draft_url}  ·  target {target_sha}

@dd-trace-py-releasers — can I get a :lgtm:? Please review and *Publish release* when ready.

_Brought to you by release automation — {script} on {script_branch_url} / {script_branch}. Run by {runner}. Try {commands_hint}._
```

Rendered example (must match this shape — **Run by**, not “initiated by”; **:lgtm:**, not :+1:):

```
:package: *Draft* patch release 4.16.4 is ready for review

https://github.com/DataDog/dd-trace-py/releases/tag/untagged-6c4169f2cee574ee7099  ·  target 593e9eaa255a

@dd-trace-py-releasers — can I get a :lgtm:? Please review and *Publish release* when ready.

_Brought to you by release automation — scripts/gh/create_gh_release.py on https://github.com/DataDog/dd-trace-py/compare/main...vlad/release-create-release-rc-script / vlad/release-create-release-rc-script. Run by vlad-scherbich. Try `patch` / `rc` / `final`._
```

## One-screen rebuild (triggers/)

1. Slack → **Workflow Builder** → open the release-announce workflow (or **Create** → **From a webhook**).
2. Webhook variables: delete `text` / `reviewer_brett` / `reviewer_emmett`. Add every key in the table above (exact names).
3. Publish webhook → put new `hooks.slack.com/triggers/...` URL in gitignored `scripts/gh/.env.local` as `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL`.
4. **Send a message** → `#apm-python-release` → replace the body with the **Exact Send-a-message body** above (User Group mention + `:lgtm:` + italic footer with **Run by**).
5. **Publish** workflow. Dry-check only:
   `uv run --with packaging python scripts/gh/create_gh_release.py draft-release --target 4.16 --version 4.16.4 --dry-run --slack --no-wait-ci`
6. Confirm dry-run JSON keys match the table. **Only then** wet `--slack` (ask before re-POST).

## Switch to Incoming Webhook (`services/`) — preferred when available

1. Slack app → **Incoming Webhooks** → Add to `#apm-python-release` → copy `hooks.slack.com/services/...`.
2. `.env.local`: `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL=https://hooks.slack.com/services/...`
3. Optional: `DD_SLACK_RELEASERS_SUBTEAM_ID=S…` so mrkdwn notifies the usergroup.
4. Dry-run `--slack` should print **Incoming Webhook mrkdwn** (full footer already correct in script). Then wet is safe.

Do **not** invent a webhook URL. Until rebuild or `services/` switch: **dry-run only — no channel re-POST.**
