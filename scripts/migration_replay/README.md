# Profiling migration replay harness

Headless replay of the CPython 3.14→3.15 Continuous Profiler migration to test
whether the #19273 runbook/skills and #20565 `cpython_delta` tooling actually
work when the answer key is withheld (blind arm).

## Pins (find-base)

| Pin | SHA | Why |
|---|---|---|
| **BASE_SHA** | `2147f61066d98d27ad96a5db717e052be7d0c824` | Parent of the first 3.15 packaging commit on `main` |
| **FIRST_315_SHA** | `8a9b5083ed7b6fea5f390bc1c3867f50b7807449` | #17959 — first `cp315` rows in `.gitlab/package.yml` (`git log origin/main --reverse -S 'cp315' -- .gitlab/package.yml`) |
| Overlay tooling | `a49560659ea4bac3e0e7e44f2c9100c404000094` | #19273 tip after Q3 (#20814) split — registry/suitespec/verify |
| Overlay delta | `6a55229d09e88ffec35a23c4a192a76620273dd8` | #20565 tip (`vlad/315-profiling-cpython-delta`) |

**Verified** on a full (non-shallow) clone at
`/Users/vlad.scherbich/go/src/github.com/DataDog/dd-trace-py`
(`git rev-parse --is-shallow-repository` → `false`). Local
`dd-trace-py-19273-tooling` remains shallow from ~2026-06-01 and cannot see
#17959 without unshallowing — do not use it as `SOURCE_REPO` for base resolution
unless it has been unshallowed.

Earlier main commits mention 3.15 (testrunner 3.15-dev #15995/#17710, template
#17791, incompatibility marker #15556) but do **not** introduce cp315 packaging.
They remain in the sandbox; ground truth starts at #17959.

The previously floated base `7277714422` (2026-07-14) is **wrong**: it is after
#17959 and after the shallow-clone root already contained cp315 wheel rows.

## Layout

| File | Role |
|---|---|
| `prepare_sandbox.sh` | Depth-1 clone at `BASE_SHA`, no remotes; overlay #19273+#20565; harness discovery overlay; blind wipe + scrub guard |
| `python_env.sh` | PATH / `PYTHON` shim for `~/.local/py315rc2` + Homebrew 3.14 (no pyenv required) |
| `scrub.yaml` | **Guard only** — abort blind arm if answer-key patterns leak into generic docs |
| `run_replay.sh` | Clean `HOME`, strip creds, `claude --bare` / `cursor-agent --sandbox`, budgets, phase pin |
| `score_replay.py` | Gates / coverage / rubric / contamination → `report.md` |
| `ground_truth.yaml` | Fact checks tagged `profiling\|shared\|ci\|packaging` |

## Arms

- **assisted** — full overlay (upper bound).
- **blind** — delete `py315_pr_catalog.md`, `analysis_314_to_315.md`,
  `PROFILING_STACK.md`; reset registry `3.15` to `checklist_template` with empty
  `layout_contracts` and `asyncio_hook: null`; run `scrub.yaml` guard on generic
  process docs. Does **not** rewrite #19273 sources.

## Interpreters (defaults; env overrides OK)

| Role | Default | Verified |
|---|---|---|
| 3.15.0rc2 prefix | `PY315RC2=~/.local/py315rc2` → `bin/python3.15` | Built locally (`3.15.0rc2`) |
| Previous minor | `PYTHON314=/opt/homebrew/bin/python3.14` | Homebrew `3.14.6` |

`prepare_sandbox.sh` / `run_replay.sh` prefer these paths. Overlay also ships
discovery that checks `PYTHON` → `PY315RC2` / Homebrew → PATH → **pyenv last**
(so a stale pyenv `3.15.0a7` cannot beat rc2). For ad-hoc smoke without pyenv:

```bash
source scripts/migration_replay/python_env.sh
# or:
scripts/migration_replay/python_env.sh --python 3.15 -- \
  scripts/run-profiling-tests --python 3.15 --quick
```

## Quick start

```bash
# 1) Sandbox (needs a full clone that contains BASE_SHA)
scripts/migration_replay/prepare_sandbox.sh \
  --arm blind \
  --out /tmp/replay-blind-1 \
  --source-repo /Users/vlad.scherbich/go/src/github.com/DataDog/dd-trace-py

# 2) Agent run (requires CURSOR_API_KEY or ANTHROPIC_API_KEY)
scripts/migration_replay/run_replay.sh \
  --runner claude \
  --arm blind \
  --sandbox /tmp/replay-blind-1 \
  --out /tmp/replay-results/blind-claude-1

# 3) Score
python3 scripts/migration_replay/score_replay.py \
  --sandbox /tmp/replay-blind-1 \
  --run-out /tmp/replay-results/blind-claude-1
```

## Isolation (run_replay.sh)

- Fresh `HOME` under `--out/home` (no user `~/.cursor` / `~/.claude`).
- Unsets `GH_TOKEN`, `GITHUB_TOKEN`, `SSH_AUTH_SOCK` (and related).
- PATH includes `${PY315RC2}/bin` and `/opt/homebrew/bin` (minimal system PATH).
- Exports `PY315RC2`, `PYTHON314`, and `PYTHON`→rc2 when the binary exists.
- claude: `--bare --max-budget-usd 40`, disallows `gh` / `git push` /
  `WebFetch(github.com/DataDog/dd-trace-py/*)`.
- cursor-agent: `--sandbox enabled`, **no** `--approve-mcps`.
- Wall-clock cap 3h (`TIMEOUT_SECS=10800`).
- Prompt pins phase to just-after-3.15.0rc2 and interpreters above.
- Agent must write `REPLAY_ACTIONS.md`, `REPLAY_FRICTION.md`, `REPLAY_PRS.md`.

## Ground truth scope

Profiling PRs after the base **plus** #17809 Tier A shared prerequisites:

`#17959`, `#19903`, `#18429`, `#17849`/`#19910`, `#19724`, `#19247`, `#19267`,
`#19843`, `#19880`, `#19861`, `#19269` (+`#19270`), `#19272`, `#19207`, `#20450`,
testrunner `#19977`/`#19907`/`#19984`.

Open items `#19995` / `#17815` are **rubric-only**.

Out of scope: #17809 Tier B integrations, #17945 CI Visibility.

## Overlay smoke (re-verified 2026-10-06 on a blind sandbox)

| Tool | Result | Notes |
|---|---|---|
| `verify … --python 3.14 --quick` | **PASS** with 3.14 venv | Editable install of base-era `ddtrace` into Homebrew 3.14 venv first |
| `run-profiling-tests --python 3.14 --check-only` | **PASS** (discovery) | Homebrew 3.14 without pyenv; needs the same venv for `ddtrace` |
| `verify … --python 3.15 --quick` | **discovery PASS** | Resolves `~/.local/py315rc2` rc2 (not pyenv `3.15.0a7`); compat result depends on editable install / port |
| `cpython_delta/inventory.py` | **PASS** | 79 symbols / 146 files on base tree |
| `cpython_delta/diff.py v3.14.0 v3.15.0rc2` | **PASS** | Against local CPython checkout with both tags |

Base still has `riotfile.py`, but the a495 tooling tip's `run-profiling-tests` discovers **suitespec** envs (`tests.suitespec`). Full (non-`--quick`) suite runs on the base sandbox need the agent to bridge that gap (or keep using `--quick` / `--check-only`). Missing editable `ddtrace` in a version-matched venv is an environment setup step, not a doc defect.

## Iter1 blockers (accurate)

- **Not a blocker:** `~/.local/py315rc2` **is built** (`3.15.0rc2`); Homebrew `python3.14` is available. Discovery no longer requires pyenv.
- Gate commands in `score_replay.py` default to skip unless `--run-commands`.
- Blind arm n=2 × 2 runners needs `CURSOR_API_KEY` / `ANTHROPIC_API_KEY` (both unset in this environment) and wall-clock budget (~$40 / 3h each).
- Base-era tree + suitespec-based runner: full `run-profiling-tests` (no `--quick`) may fail until suitespec/3.15 matrix exists in-tree.
- Base-era editable install + C extensions on 3.15rc2 may fail until the agent ports code (expected for replay, not a harness blocker).
- prof-correctness Docker on Apple Silicon is unknown; may need Linux workspace.
