#!/usr/bin/env bash
# Run one headless migration-replay agent against a prepared sandbox.
#
# Isolation:
#   - clean HOME (no ~/.cursor / ~/.claude skills, rules, memories)
#   - strip GH_TOKEN / GITHUB_TOKEN / SSH_AUTH_SOCK
#   - claude --bare; cursor-agent --sandbox without --approve-mcps
#   - budgets: ~$40 / 3h
#   - phase pinned in prompt to just-after-3.15.0rc2
#
# Usage:
#   scripts/migration_replay/run_replay.sh \
#     --runner cursor|claude \
#     --arm assisted|blind \
#     --sandbox /tmp/replay-blind \
#     --out /tmp/replay-results/blind-cursor-1
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RUNNER=""
ARM=""
SANDBOX=""
OUT_DIR=""
MAX_BUDGET_USD="${MAX_BUDGET_USD:-40}"
TIMEOUT_SECS="${TIMEOUT_SECS:-10800}"  # 3h
PY315RC2="${PY315RC2:-${HOME}/.local/py315rc2}"
CURSOR_BIN="${CURSOR_BIN:-cursor-agent}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"

usage() {
  cat <<'EOF'
Usage: run_replay.sh --runner cursor|claude --arm assisted|blind \
                     --sandbox DIR --out DIR

Environment:
  MAX_BUDGET_USD (default 40), TIMEOUT_SECS (default 10800),
  PY315RC2, CURSOR_API_KEY / ANTHROPIC_API_KEY, CURSOR_BIN, CLAUDE_BIN
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runner) RUNNER="$2"; shift 2 ;;
    --arm) ARM="$2"; shift 2 ;;
    --sandbox) SANDBOX="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "$RUNNER" != "cursor" && "$RUNNER" != "claude" ]]; then
  echo "ERROR: --runner must be cursor or claude" >&2
  exit 2
fi
if [[ "$ARM" != "assisted" && "$ARM" != "blind" ]]; then
  echo "ERROR: --arm must be assisted or blind" >&2
  exit 2
fi
if [[ -z "$SANDBOX" || ! -d "$SANDBOX/.git" ]]; then
  echo "ERROR: --sandbox must be a prepared git checkout" >&2
  exit 2
fi
if [[ -z "$OUT_DIR" ]]; then
  echo "ERROR: --out DIR required" >&2
  exit 2
fi
if [[ -e "$OUT_DIR" ]]; then
  echo "ERROR: --out already exists: ${OUT_DIR}" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
CLEAN_HOME="${OUT_DIR}/home"
mkdir -p "${CLEAN_HOME}"

# Seed minimal git config in clean HOME (no credentials helper).
mkdir -p "${CLEAN_HOME}/.config/git"
cat >"${CLEAN_HOME}/.gitconfig" <<'EOF'
[user]
    name = migration-replay-agent
    email = migration-replay-agent@localhost
[commit]
    gpgsign = false
[credential]
    helper =
EOF

# Prompt artifacts the agent must produce.
PROMPT_FILE="${OUT_DIR}/PROMPT.md"
cat >"$PROMPT_FILE" <<EOF
Add Continuous Profiler support for CPython 3.15 to this repo, following
\`AGENTS.md\` and the \`migrate-profiling-new-cpython\` skill.

## Phase pin (do not re-derive from the real clock or PEP page)
Treat today as **just after 3.15.0rc2 shipped**. The 3.15 interpreter for local
work is at \`${PY315RC2}\` (bin/python). Previous minor: system/Homebrew 3.14.

## Isolation rules (hard)
- Do not call \`gh\`, \`git push\`, or fetch from github.com/DataDog/dd-trace-py PRs.
- Do not read paths outside this workspace.
- Credentials are unavailable; do not try to authenticate.

## What to implement locally
- Alpha and beta work: implement and run locally where possible.
- RC and final / external repos (engraver, images, dd-source, SSI publish):
  do **not** execute. Write exact files, diffs, and commands into
  \`REPLAY_ACTIONS.md\` under clear headings.

## Required output files in the repo root
1. \`REPLAY_ACTIONS.md\` — external + \`## manual\` checklist items a human would do.
2. \`REPLAY_FRICTION.md\` — every wrong/ambiguous/missing/stale doc passage with file:line.
3. \`REPLAY_PRS.md\` — PRs you would open, in order, with files in each (score against
   the runbook "Expected changes (in order)").

Arm: **${ARM}**. Runner: **${RUNNER}**.
EOF

# Copy prompt into sandbox so the agent sees it as a workspace file too.
cp "$PROMPT_FILE" "${SANDBOX}/REPLAY_PROMPT.md"

# Baseline for diff capture.
BASELINE_SHA="$(git -C "$SANDBOX" rev-parse HEAD)"
echo "$BASELINE_SHA" >"${OUT_DIR}/baseline_sha.txt"

# Strip credentials / agent home leakage.
RUN_ENV=(
  "HOME=${CLEAN_HOME}"
  "PATH=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:${HOME}/.local/bin:${HOME}/.volta/bin"
  "PY315RC2=${PY315RC2}"
  "LANG=C.UTF-8"
  "LC_ALL=C.UTF-8"
)
# Keep only the API key needed for the chosen runner.
if [[ "$RUNNER" == "cursor" ]]; then
  if [[ -z "${CURSOR_API_KEY:-}" ]]; then
    echo "ERROR: CURSOR_API_KEY is required for cursor runner" >&2
    exit 1
  fi
  RUN_ENV+=("CURSOR_API_KEY=${CURSOR_API_KEY}")
else
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "ERROR: ANTHROPIC_API_KEY is required for claude runner" >&2
    exit 1
  fi
  RUN_ENV+=("ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
fi

# env -i already drops GH_TOKEN / GITHUB_TOKEN / SSH_AUTH_SOCK / git askpass.
# Do not re-export them in RUN_ENV.

TRANSCRIPT="${OUT_DIR}/transcript.jsonl"
META="${OUT_DIR}/meta.json"
START_EPOCH="$(date +%s)"

PROMPT_TEXT="$(cat "$PROMPT_FILE")"

echo "run_replay: runner=${RUNNER} arm=${ARM} sandbox=${SANDBOX} out=${OUT_DIR}"
echo "  clean_home=${CLEAN_HOME}"
echo "  budget_usd=${MAX_BUDGET_USD} timeout_s=${TIMEOUT_SECS}"
echo "  baseline=${BASELINE_SHA}"

# macOS may lack GNU timeout; prefer gtimeout then Python watchdog.
run_with_timeout() {
  if command -v gtimeout >/dev/null 2>&1; then
    gtimeout --signal=TERM "${TIMEOUT_SECS}" "$@"
  elif command -v timeout >/dev/null 2>&1; then
    timeout --signal=TERM "${TIMEOUT_SECS}" "$@"
  else
    python3 - "$TIMEOUT_SECS" "$@" <<'PY'
import os, signal, subprocess, sys
secs = int(sys.argv[1])
cmd = sys.argv[2:]
proc = subprocess.Popen(cmd)
try:
    rc = proc.wait(timeout=secs)
except subprocess.TimeoutExpired:
    proc.send_signal(signal.SIGTERM)
    try:
        rc = proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = 124
    else:
        rc = 124
sys.exit(rc if rc is not None else 124)
PY
  fi
}

set +e
if [[ "$RUNNER" == "cursor" ]]; then
  # --sandbox enabled; do NOT pass --approve-mcps. No MCP config in clean HOME.
  # cursor-agent has no --max-budget-usd; enforce wall clock only.
  run_with_timeout \
    env -i "${RUN_ENV[@]}" \
      "${CURSOR_BIN}" -p --force \
        --sandbox enabled \
        --workspace "$SANDBOX" \
        "$PROMPT_TEXT" \
    >"${TRANSCRIPT}" 2>"${OUT_DIR}/stderr.log"
  RC=$?
else
  # --bare: no user hooks/memories/keychain. Skills come from sandbox .claude/skills/.
  run_with_timeout \
    env -i "${RUN_ENV[@]}" \
      "${CLAUDE_BIN}" -p \
        --bare \
        --output-format stream-json \
        --permission-mode acceptEdits \
        --max-budget-usd "${MAX_BUDGET_USD}" \
        --disallowedTools "Bash(gh *)" "Bash(git push*)" "WebFetch(github.com/DataDog/dd-trace-py/*)" \
        --add-dir "$SANDBOX" \
        "$PROMPT_TEXT" \
    >"${TRANSCRIPT}" 2>"${OUT_DIR}/stderr.log"
  RC=$?
fi
set -e

END_EPOCH="$(date +%s)"
WALL=$((END_EPOCH - START_EPOCH))

# Capture sandbox diff + required artifacts.
git -C "$SANDBOX" diff "${BASELINE_SHA}" >"${OUT_DIR}/sandbox.diff" || true
git -C "$SANDBOX" status --porcelain >"${OUT_DIR}/sandbox.status.txt" || true
for f in REPLAY_ACTIONS.md REPLAY_FRICTION.md REPLAY_PRS.md; do
  if [[ -f "${SANDBOX}/${f}" ]]; then
    cp "${SANDBOX}/${f}" "${OUT_DIR}/${f}"
  else
    echo "(missing)" >"${OUT_DIR}/${f}"
  fi
done

python3 - "$META" <<PY
import json, sys
from pathlib import Path
meta = {
    "runner": "${RUNNER}",
    "arm": "${ARM}",
    "sandbox": "${SANDBOX}",
    "baseline_sha": "${BASELINE_SHA}",
    "exit_code": int("${RC}"),
    "wall_seconds": int("${WALL}"),
    "max_budget_usd": float("${MAX_BUDGET_USD}"),
    "timeout_secs": int("${TIMEOUT_SECS}"),
    "py315rc2": "${PY315RC2}",
    "transcript": str(Path("${TRANSCRIPT}")),
}
Path(sys.argv[1]).write_text(json.dumps(meta, indent=2) + "\n")
print(json.dumps(meta, indent=2))
PY

echo "run_replay finished rc=${RC} wall=${WALL}s → ${OUT_DIR}"
exit "$RC"
