# Interim: structured Workflow Builder webhook

**Status:** interim only. Prefer a shared Incoming Webhook (`hooks.slack.com/services/...`) in `scripts/gh/.env.local` as `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL` (see `.env.local.example`). Freeform mrkdwn in a single Workflow `text` variable shows as literals; do not use that.

When `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL` is a `hooks.slack.com/triggers/...` URL, `create_gh_release.py` POSTs **flat string fields** (not `{"text": "<mrkdwn>"}`).

**API note:** Slack Workflow Builder definitions are **not** editable via the GitHub/`gh` APIs we use here. Rebuild is a human click path in Slack (below), or switch `.env.local` to a `services/` Incoming Webhook URL.

## Payload keys (all strings) — must match Workflow webhook variables exactly

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

**Removed (do not keep):** `reviewer_brett` / `reviewer_emmett` User ID vars; single freeform `text` mrkdwn blob. Ping `@dd-trace-py-releasers` (static User Group mention). Use **`:lgtm:`** — not `:+1:` / thumbs-up.

Dry-run (`--dry-run --slack`) with a `triggers/` URL prints **structured JSON + a Workflow-rendered mock** using these keys. Wet POST uses the same JSON. Until the Workflow is rebuilt, wet posts still look wrong.

## Switch to Incoming Webhook (`services/`) — preferred when available

If you can create/use an Incoming Webhook for `#apm-python-release`:

1. Slack app → **Incoming Webhooks** → Add to `#apm-python-release` → copy `hooks.slack.com/services/...`.
2. Put it in gitignored `scripts/gh/.env.local`:
   `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL=https://hooks.slack.com/services/...`
3. Optional: `DD_SLACK_RELEASERS_SUBTEAM_ID=S…` so mrkdwn notifies the usergroup.
4. Dry-run `--slack` should print **Incoming Webhook mrkdwn** (not Workflow JSON). Then wet is safe.

Do **not** invent a webhook URL. If you only have `triggers/`, stay on the interim path and rebuild the Workflow.

## Rebuild the Workflow (click path) — required while `.env.local` is `triggers/`

1. Slack → **Workflow Builder** → open the release announce workflow (or **Create workflow** → **From a webhook**).
2. Under **From a webhook**, delete the old single `text` variable (if present) and any `reviewer_brett` / `reviewer_emmett` variables.
3. **Add variables** with the **exact** names in the table above (all Text / Channel ID as noted).
4. Publish / copy the new `hooks.slack.com/triggers/...` URL into `scripts/gh/.env.local` as `DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL` (gitignored).
5. **Add step** → **Send a message to a channel** → `#apm-python-release`.
6. Message body (static formatting in the editor; **Insert a variable** for fields):

   - Line 1: `:package:` then bold **Draft** then insert `kind` then insert `version` then ` is ready for review`
   - Line 2: insert `draft_url` · `target` then insert `target_sha`
   - Line 3: **static User Group mention** `@dd-trace-py-releasers` (Insert → usergroup — do **not** insert `releasers_handle` as plain text). Then ` — can I get a ` then **`:lgtm:`** (not `:+1:`) then `? Please review and` then bold **Publish release** then ` when ready.`
   - Footer (**italic** in the editor — do not omit): static `Brought to you by release automation — ` then insert `script` then ` on ` then insert `script_branch_url` then ` / ` then insert `script_branch` then `. Run by ` then insert `runner` then `. Try ` then insert `commands_hint` then `.`

   Rendered footer must look like (Incoming Webhook mrkdwn equivalent):

   `_Brought to you by release automation — `scripts/gh/create_gh_release.py` on https://github.com/DataDog/dd-trace-py/compare/main...vlad/release-create-release-rc-script / vlad/release-create-release-rc-script. Run by vlad-scherbich. Try `patch` / `rc` / `final`._`

7. **Publish** the workflow. Run `draft-release … --dry-run --slack` and confirm JSON keys match (including `script`, `script_branch`, `script_branch_url`, `commands_hint`, `runner`). Only then wet-post.

Until steps 1–7 are done (or you switch to `services/`), do **not** wet-post to `#apm-python-release` — dry-run preview only.
