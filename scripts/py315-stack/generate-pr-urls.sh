#!/usr/bin/env bash
# Generate GitHub compare URLs + PR bodies with prev/next navigation for the py3.15 stack.
# EMU blocks `gh pr create`; use open-all-draft-prs.sh or compare URLs in DRAFT_PRS.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

ENV_FILE="$(dirname "$0")/pr-numbers.env"
# shellcheck disable=SC1090
source "$ENV_FILE"

STACK_MAX=14
REPO="DataDog/dd-trace-py"
BASE_URL="https://github.com/${REPO}"

pr_num() {
  local n=$1
  local var="PR${n}"
  echo "${!var:-}"
}

pr_url() {
  local n=$1
  local num
  num="$(pr_num "$n")"
  if [[ -n "$num" ]]; then
    echo "${BASE_URL}/pull/${num}"
  else
    echo ""
  fi
}

nav_line() {
  local idx=$1
  local prev_n=$((idx - 1))
  local next_n=$((idx + 1))
  local prev next prev_num next_num prev_url next_url

  if [[ $prev_n -ge 1 ]]; then
    prev_num="$(pr_num "$prev_n")"
    prev_url="$(pr_url "$prev_n")"
    if [[ -n "$prev_num" && -n "$prev_url" ]]; then
      prev="[#${prev_num}](${prev_url})"
    else
      prev="_#${prev_n} (pending)_"
    fi
  else
    prev="—"
  fi

  if [[ $next_n -le $STACK_MAX ]]; then
    next_num="$(pr_num "$next_n")"
    next_url="$(pr_url "$next_n")"
    if [[ -n "$next_num" && -n "$next_url" ]]; then
      next="[#${next_num}](${next_url})"
    else
      next="_#${next_n} (pending)_"
    fi
  else
    next="—"
  fi

  echo "**prev:** ${prev} | **next:** ${next}"
}

urlencode() {
  python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.stdin.read()))'
}

write_pr_entry() {
  local idx=$1
  local total=$2
  local head=$3
  local base=$4
  local title=$5
  local summary=$6
  local action_note=${7:-}
  local test_plan=${8:-"- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch"}

  local nav body_file body_encoded title_encoded compare_url existing pr_var existing_num

  nav="$(nav_line "$idx")"
  body_file="${OUT_DIR}/$(printf '%02d' "$idx")-${head//\//-}.md"

  cat > "$body_file" <<EOF
${nav}

## Summary

${summary}
${action_note}

## Test plan

${test_plan}

EOF

  body_encoded="$(cat "$body_file" | urlencode)"
  title_encoded="$(printf '%s' "$title" | urlencode)"
  compare_url="${BASE_URL}/compare/${base}...${head}?expand=1&title=${title_encoded}&body=${body_encoded}&draft=1"

  existing="$(pr_url "$idx")"
  pr_var="PR${idx}"
  existing_num="${!pr_var:-}"
  {
    echo "## ${idx}/${total} — \`${head}\` → \`${base}\`"
    echo ""
    if [[ -n "$existing" && -n "$existing_num" ]]; then
      echo "**Existing PR:** [#${existing_num}](${existing}) — set base to \`${base}\`, mark draft, paste body from [\`pr-bodies/$(basename "$body_file")\`](pr-bodies/$(basename "$body_file"))."
    else
      echo "**Open draft:** [Create PR on GitHub](${compare_url})"
    fi
    echo ""
    echo "${nav}"
    echo ""
    echo "<details><summary>PR body (copy)</summary>"
    echo ""
    cat "$body_file"
    echo ""
    echo "</details>"
    echo ""
  } >> "$INDEX"

  printf '%s\n' "$compare_url" > "${OUT_DIR}/$(printf '%02d' "$idx")-compare.url"
}

# idx|head|base|title|summary|action_note (optional, use · for empty)
ENTRIES=(
  "1|gab/315-monitoring-multiplexer|main|feat(internal): sys.monitoring multiplexer for Python 3.15 (split 1/14)|Introduces \`ddtrace.internal.monitoring\` — shared \`sys.monitoring\` multiplexer (Gab). Split from #17849.|·"
  "2|chore/315-wrapping-context|gab/315-monitoring-multiplexer|chore(wrapping): Python 3.15 wrapping context support (split 2/14)|Wrapping context + bytecode_injection for 3.15 (Gab + await/send fix).|Retarget existing **#17849** to base \`gab/315-monitoring-multiplexer\`."
  "3|vlad/315-ci-matrix|gab/315-wrapping-context|ci(py3.15): add 3.15 to riot matrix with gated suites (split 3/14)|Riotfile 3.15, docker testrunner, suite gating.|·"
  "4|vlad/315-ci-autoregen-lockfiles|vlad/315-ci-matrix|ci: auto-commit regenerated riot lockfiles on PR branches (split 4/14)|Self-healing lockfile drift on PR branches.|·"
  "5|vlad/315-official-support|vlad/315-ci-autoregen-lockfiles|chore(py3.15): declare official 3.15 support in packaging (split 5/14)|pyproject.toml, requirements.csv, riot lockfiles.|·"
  "6|vlad/315-peripheral-compat|vlad/315-official-support|fix(py3.15): peripheral compat for profiling, logging, appsec (split 6/14)|Graceful degradation + test skips outside wrapping core.|·"
  "7|vlad/profiling-native-test-install-subdir|vlad/315-peripheral-compat|chore(profiling): native test install subdirs (PROF-14200) (split 7/14)|INSTALL_SUBDIR for py3.15 native tests.|·"
  "8|vlad/ddtracepy-315-profiling-native|vlad/profiling-native-test-install-subdir|chore(profiling): native C++/Rust py3.15 ABI support (split 8/14)|Native profiling py3.15 ABI (Echion frame state, cmake).|·"
  "9|vlad/ddtracepy-315-profiling-collectors|vlad/ddtracepy-315-profiling-native|chore(profiling): update Python profiling collectors for py3.15 (split 9/14)|Collector updates for 3.15.|·"
  "10|vlad/ddtracepy-315-profiling-only|vlad/ddtracepy-315-profiling-collectors|ci(profiling): wire py3.15 into build matrix and CI (split 10/14)|Profiling CI matrix and setup.py gating.|·"
  "11|vlad/ddtracepy-315-profiling-asyncio-monitoring|vlad/ddtracepy-315-profiling-only|refactor(profiling): sys.monitoring asyncio path for py3.15 (split 11/14)|Replace bytecode wrapping with \`sys.monitoring\` in \`_asyncio.py\`.|·"
  "12|vlad/315-profiling-dev-tooling|vlad/ddtracepy-315-profiling-asyncio-monitoring|docs(profiling): py3.15 dev tooling and CPython upgrade runbook (split 12/14)|Profiling bring-up scripts, compatibility baselines, Echion migration runbook (GAP-03).|·"
  "13|vlad/315-lib-injection-ssi|vlad/315-profiling-dev-tooling|chore(py-315): enable lib-injection SSI for Python 3.15 (split 13/14)|SSI allow-list + wheel download for 3.15 auto-instrumentation (GAP-01). Closes #17813.|·"
  "14|vlad/315-profiling-release-note|vlad/315-lib-injection-ssi|docs(releasenotes): profiling Python 3.15 support note (split 14/14)|Customer-facing Reno fragment for profiling on 3.15 (GAP-02).|·"
)

OUT_DIR="$(dirname "$0")/pr-bodies"
mkdir -p "$OUT_DIR"
INDEX="$(dirname "$0")/DRAFT_PRS.md"

{
  echo "# Python 3.15 stack — draft PRs (1–14)"
  echo ""
  echo "EMU blocks \`gh pr create\` / \`gh pr edit\`. Run [\`sync-pr-numbers.sh\`](sync-pr-numbers.sh) then [\`open-all-draft-prs.sh\`](open-all-draft-prs.sh)."
  echo "Set numbers: \`./sync-pr-numbers.sh PR1=19250 PR6=19255 ...\` (preserves existing, fills gaps from GitHub)."
  echo ""
} > "$INDEX"

for entry in "${ENTRIES[@]}"; do
  IFS='|' read -r idx head base title summary action_note <<< "$entry"
  if [[ "$action_note" == "·" ]]; then action_note=""; else
    action_note=$'\n\n'"${action_note}"
  fi

  test_plan="- [ ] CI green on this branch
- [ ] Stack merges cleanly into the next PR's base branch"
  case "$idx" in
    12) test_plan="- [ ] \`scripts/run-profiling-tests --check-only\` passes on 3.15 (when available)
- [ ] Docs build / link check" ;;
    13) test_plan="- [ ] lib-injection CI green" ;;
    14) test_plan="- [ ] \`riot run reno\` validates fragment
- [ ] Merge with PR that lifts profiling native gate for 3.15" ;;
  esac

  write_pr_entry "$idx" "$STACK_MAX" "$head" "$base" "$title" "$summary" "$action_note" "$test_plan"
done

echo "Wrote ${INDEX} and ${OUT_DIR}/*.md"
