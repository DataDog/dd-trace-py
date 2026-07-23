#!/usr/bin/env bash
# Query GitHub for PR numbers by head branch and refresh pr-numbers.env + bodies.
# Preserves manually set PRn= values; only fills empty slots from GitHub.
#
# Usage:
#   ./sync-pr-numbers.sh              # sync empty slots from GitHub
#   ./sync-pr-numbers.sh PR6=19250    # set PR6 then sync + regenerate
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$DIR/pr-numbers.env"

declare -A EXISTING=()
if [[ -f "$ENV_FILE" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^PR[0-9]+$ ]] || continue
    EXISTING["$key"]="$value"
  done < <(grep -E '^PR[0-9]+=' "$ENV_FILE" || true)
fi

for arg in "$@"; do
  if [[ "$arg" =~ ^(PR[0-9]+)=(.*)$ ]]; then
    EXISTING["${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"
  else
    echo "Usage: $0 [PRn=number ...]" >&2
    exit 1
  fi
done

declare -a BRANCHES=(
  "PR1|gab/315-monitoring-multiplexer"
  "PR2|chore/315-wrapping-context"
  "PR3|vlad/315-ci-matrix"
  "PR4|vlad/315-ci-autoregen-lockfiles"
  "PR5|vlad/315-official-support"
  "PR6|vlad/315-peripheral-compat"
  "PR7|vlad/profiling-native-test-install-subdir"
  "PR8|vlad/ddtracepy-315-profiling-native"
  "PR9|vlad/ddtracepy-315-profiling-collectors"
  "PR10|vlad/ddtracepy-315-profiling-only"
  "PR11|vlad/ddtracepy-315-profiling-asyncio-monitoring"
  "PR12|vlad/315-profiling-dev-tooling"
  "PR13|vlad/315-lib-injection-ssi"
  "PR14|vlad/315-profiling-release-note"
)

lookup_pr() {
  local branch=$1
  gh pr list --head "$branch" --state all --json number --jq 'sort_by(.number) | last | .number // empty' 2>/dev/null || true
}

TMP="$(mktemp)"
{
  echo "# PR numbers for stack navigation. ./sync-pr-numbers.sh [PRn=num ...] then regenerates bodies."
} > "$TMP"

for entry in "${BRANCHES[@]}"; do
  key="${entry%%|*}"
  branch="${entry#*|}"
  num="${EXISTING[$key]:-}"

  if [[ -z "$num" ]]; then
    num="$(lookup_pr "$branch")"
    if [[ -z "$num" && "$key" == "PR2" ]]; then
      num="$(lookup_pr "gab/315-wrapping-context")"
    fi
  fi

  echo "${key}=${num}" >> "$TMP"
  if [[ -n "$num" ]]; then
    printf '  %s=%s  (%s)\n' "$key" "$num" "$branch"
  else
    printf '  %s=       (%s) — no PR yet\n' "$key" "$branch"
  fi
done

mv "$TMP" "$ENV_FILE"
echo ""
echo "Wrote ${ENV_FILE}"
"$DIR/generate-pr-urls.sh"
