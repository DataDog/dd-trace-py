# Interim: structured Workflow Builder webhook

**Status:** interim only. Prefer a shared Incoming Webhook (`hooks.slack.com/services/...`) — see the FR steps in the release chat / AGENTS notes. Freeform mrkdwn in a single Workflow `text` variable shows as literals; do not use that.

When `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL` is a `hooks.slack.com/triggers/...` URL, `create_gh_release.py` POSTs **flat string fields** (not `{"text": "<mrkdwn>"}`).

## Payload keys (all strings)

| Key | Example | Workflow variable type |
| --- | --- | --- |
| `version` | `4.15.8` | Text |
| `kind` | `patch release` | Text |
| `draft_url` | `https://github.com/.../releases/tag/v4.15.8` | Text |
| `target_sha` | `503be289517c…` | Text |
| `releasers_handle` | `dd-trace-py-releasers` | Text (optional; prefer static User Group below) |
| `runner` | `vlad-scherbich` | Text (GitHub login) |
| `channel_id` | `C04MK6NNDG9` | Channel ID (optional) |
| `script` | `scripts/gh/create_gh_release.py` | Text |
| `script_branch` | `vlad/release-create-release-rc-script` | Text |
| `script_branch_url` | `https://github.com/DataDog/dd-trace-py/compare/main...vlad/release-create-release-rc-script` | Text |
| `commands_hint` | `patch / rc / final` | Text |

**Removed:** `reviewer_brett` / `reviewer_emmett` User ID vars. Ping the whole releasers group instead.

## Rebuild the Workflow (click path)

1. Slack → **Workflow Builder** → open the release announce workflow (or create one: **Create workflow** → **From a webhook**).
2. Under **From a webhook**, delete the old single `text` variable (if present) and any `reviewer_brett` / `reviewer_emmett` variables.
3. **Add variables** with the **exact** names above and types from the table.
4. Publish / copy the new `hooks.slack.com/triggers/...` URL into `scripts/gh/.env.local` as `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL` (keep the file gitignored).
5. **Add step** → **Send a message to a channel** → `#apm-python-release`.
6. Message body (type static formatting in the editor; insert variables with **Insert a variable**):

   - Line 1: `:package:` then bold **Draft** then insert `kind` then insert `version` then ` is ready for review`
   - Line 2: insert `draft_url` (bare URL usually auto-links) · `target` then insert `target_sha`
   - Line 3: type a **static User Group mention** `@dd-trace-py-releasers` (Insert → mention the usergroup — do **not** rely on inserting `releasers_handle` as plain text; that does not notify). Then ` — can I get a ` then **`:lgtm:`** (not `:+1:` / thumbs-up) then `? Please review and` then bold **Publish release** then ` when ready.`
   - Footer (italic in the editor): `Brought to you by release automation — ` then insert `script` then ` on ` then insert `script_branch_url` / `script_branch` then ` . Run by ` then insert `runner` then ` . Try ` then insert `commands_hint` then `.`

7. **Publish** the workflow. Dry-run the script first (`--dry-run --slack`) and confirm the JSON keys match. Only then wet-post.

When `.env.local` still has a `triggers/` URL, `--dry-run --slack` prints **structured JSON + a Workflow-rendered mock** (not Incoming Webhook mrkdwn). Freeform mrkdwn preview is only for `services/` URLs.

Until steps 1–7 are done, wet posts to the old Workflow will still look wrong even though the script no longer sends a mrkdwn blob.
