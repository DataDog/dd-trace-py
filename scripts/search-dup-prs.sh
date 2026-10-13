#!/usr/bin/env bash
# Search this GitHub repo for open/draft PRs and PRs merged in the last 90 days
# that might already cover the current branch's Python profiling issue
# or feature.
#
# Usage: scripts/search-dup-prs.sh [keyword ...]
# Extra keywords are the search query (preferred). If omitted, the script
# uses the branch name, issue ids, and the latest commit subject.
# Execute this; do not only read it.
#
# Scope: Python profiling paths in DataDog/dd-trace-py only. Other
# repos and non-profiling diffs print "## scope: skip" and exit 0
# without searching. Expanding to all of dd-trace-py is a later flip.
#
# Prints candidates. Does not score them and does not push. Exit 0 on a
# completed search or a scope skip. Exit 1 when git/gh is missing, the
# repo cannot be resolved, or the default branch / merge-base cannot.

set -euo pipefail

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: not a git repository" >&2
  exit 1
fi

origin_repo() {
  local url
  url="$(git remote get-url origin 2>/dev/null || true)"
  url="${url%.git}"
  if [[ "${url}" =~ github.com[:/]+([^/]+/[^/]+)$ ]]; then
    echo "${BASH_REMATCH[1]}"
  fi
}

REPO="$(origin_repo)"
if [[ -z "${REPO}" ]]; then
  REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
fi

BRANCH="$(git branch --show-current 2>/dev/null || true)"
FIRST_PUSH="yes"
if git rev-parse -q --verify '@{u}' >/dev/null 2>&1; then
  FIRST_PUSH="no"
fi

DEFAULT_BRANCH="$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name 2>/dev/null || echo main)"
if git rev-parse --verify "origin/${DEFAULT_BRANCH}" >/dev/null 2>&1; then
  BASE_REF="origin/${DEFAULT_BRANCH}"
elif git rev-parse --verify "${DEFAULT_BRANCH}" >/dev/null 2>&1; then
  BASE_REF="${DEFAULT_BRANCH}"
else
  echo "error: cannot resolve default branch '${DEFAULT_BRANCH}' (tried origin/${DEFAULT_BRANCH} and ${DEFAULT_BRANCH})" >&2
  exit 1
fi

if ! MERGE_BASE="$(git merge-base "${BASE_REF}" HEAD 2>/dev/null)"; then
  echo "error: cannot resolve merge-base of ${BASE_REF} and HEAD" >&2
  exit 1
fi

# Python profiling paths only (suitespec @profiling + CODEOWNERS Profiling
# + existing prof-*.yml / profiling SLO). Do not invent extras.
is_profiling_path() {
  local p="$1"
  case "${p}" in
    ddtrace/profiling|ddtrace/profiling/*) return 0 ;;
    ddtrace/internal/datadog/profiling|ddtrace/internal/datadog/profiling/*) return 0 ;;
    tests/profiling|tests/profiling/*) return 0 ;;
    ddtrace/internal/settings/profiling.py|ddtrace/internal/settings/profiling.pyi) return 0 ;;
    ddtrace/_trace/processor/endpoint_call_counter.py) return 0 ;;
    .github/workflows/prof-*.yml) return 0 ;;
    .github/workflows/profiling-native.yml) return 0 ;;
    .github/workflows/pytorch_gpu_tests.yml) return 0 ;;
    .gitlab/tests/profiling.yml) return 0 ;;
    .gitlab/benchmarks/slos/profiling-python.yml) return 0 ;;
  esac
  return 1
}

SCOPE="skip"
PROFILING_FILES=()
while IFS= read -r path; do
  [[ -z "${path}" ]] && continue
  if is_profiling_path "${path}"; then
    SCOPE="profiling"
    PROFILING_FILES+=("${path}")
  fi
done < <(git diff --name-only "${MERGE_BASE}...HEAD" 2>/dev/null || true)

echo "## repo: ${REPO}"
echo "## branch: ${BRANCH:-unknown}"
echo "## first-push: ${FIRST_PUSH}"
echo "## merge-base: ${MERGE_BASE}"
if [[ "${REPO}" != "DataDog/dd-trace-py" ]]; then
  echo "## scope: skip (not DataDog/dd-trace-py; Python profiling only)"
  exit 0
fi
if [[ "${SCOPE}" != "profiling" ]]; then
  echo "## scope: skip (not Python profiling; expand-later, not now)"
  exit 0
fi
echo "## scope: profiling"
echo "## profiling-files:"
printf '%s\n' "${PROFILING_FILES[@]}"
echo

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh is not on PATH" >&2
  exit 1
fi

SINCE="$(date -u -v-90d +%Y-%m-%d 2>/dev/null || date -u -d '90 days ago' +%Y-%m-%d)"

STOPWORDS_RE='^(fix|fixes|fixed|chore|feat|feature|ci|test|tests|wip|tmp|temp|draft|pr|the|a|an|of|to|for|and|or|in|on|at|by|with|from|into|vs|update|updates|add|adds|added)$'

extract_tokens() {
  local raw="$1"
  echo "${raw}" \
    | tr '[:upper:]' '[:lower:]' \
    | tr -cs 'a-z0-9-' '\n' \
    | sed 's/^-*//; s/-*$//' \
    | awk -v re="${STOPWORDS_RE}" '
        length($0) < 3 { next }
        $0 ~ re { next }
        $0 ~ /^[a-z]+-[0-9]+$/ { print; next }
        {
          n = split($0, parts, "-")
          if (n > 1) {
            for (i = 1; i <= n; i++) {
              if (length(parts[i]) >= 3 && parts[i] !~ re) print parts[i]
            }
          } else {
            print
          }
        }'
}

uniq_terms() {
  awk 'NF && !seen[$0]++'
}

AUTO_FILE="$(mktemp)"
EXTRA_FILE="$(mktemp)"
trap 'rm -f "${AUTO_FILE}" "${EXTRA_FILE}"' EXIT

{
  extract_tokens "${BRANCH}"
  git log --format='%s' -1 "${MERGE_BASE}..HEAD" 2>/dev/null | while IFS= read -r line; do
    extract_tokens "${line}"
  done
  git log --format='%s%n%b' "${MERGE_BASE}..HEAD" 2>/dev/null \
    | grep -oE '#[0-9]+|[A-Z][A-Z]+-[0-9]+' \
    | tr -d '#' \
    | tr '[:upper:]' '[:lower:]' || true
} | uniq_terms > "${AUTO_FILE}"

{
  for extra in "$@"; do
    extract_tokens "${extra}"
  done
} | uniq_terms > "${EXTRA_FILE}"

mapfile -t AUTO_TERMS < "${AUTO_FILE}" || true
mapfile -t EXTRA_TERMS < "${EXTRA_FILE}" || true

echo "## merged-since: ${SINCE}"
if [[ ${#EXTRA_TERMS[@]} -gt 0 ]]; then
  echo "## extra-terms: ${EXTRA_TERMS[*]}"
else
  echo "## extra-terms: (none)"
fi
if [[ ${#AUTO_TERMS[@]} -gt 0 ]]; then
  echo "## auto-terms: ${AUTO_TERMS[*]}"
else
  echo "## auto-terms: (none)"
fi
echo

run_search() {
  local label="$1"
  shift
  echo "## ${label}"
  echo "## query: $*"
  if ! gh search prs "$@" --json number,title,url,state,isDraft \
    --jq '.[] | "#\(.number) \(.state)\(if .isDraft then "/draft" else "" end) \(.title) \(.url)"'; then
    echo "(search failed)"
  fi
  echo
}

echo "## open (includes drafts)"
gh pr list --repo "${REPO}" --state open --limit 30 \
  --json number,title,url,isDraft,headRefName \
  --jq '.[] | "#\(.number) \(if .isDraft then "draft" else "open" end) \(.headRefName) \(.title) \(.url)"'
echo

# Extra keywords are the search. Fall back to a short auto query so a
# first push still searches when the agent passed nothing.
QUERY=()
if [[ ${#EXTRA_TERMS[@]} -gt 0 ]]; then
  QUERY=("${EXTRA_TERMS[@]:0:5}")
elif [[ ${#AUTO_TERMS[@]} -gt 0 ]]; then
  QUERY=("${AUTO_TERMS[@]:0:4}")
fi

if [[ ${#QUERY[@]} -gt 0 ]]; then
  run_search "search open/draft by terms" --repo "${REPO}" --state open --limit 20 "${QUERY[@]}"
  run_search "search merged since ${SINCE} by terms" --repo "${REPO}" --merged --merged-at ">=${SINCE}" --limit 20 "${QUERY[@]}"
else
  echo "## search open/draft by terms"
  echo "(skipped — no distinctive terms)"
  echo
  echo "## search merged since ${SINCE} by terms"
  echo "(skipped — no distinctive terms)"
  echo
fi

# Issue / ticket ids: search without the 90-day cap (already-landed work
# is often older than that).
ISSUE_IDS=()
for t in "${EXTRA_TERMS[@]+"${EXTRA_TERMS[@]}"}" "${AUTO_TERMS[@]+"${AUTO_TERMS[@]}"}"; do
  if [[ "${t}" =~ ^[0-9]{3,}$ || "${t}" =~ ^[a-z]+-[0-9]+$ ]]; then
    ISSUE_IDS+=("${t}")
  fi
done
if [[ ${#ISSUE_IDS[@]} -gt 0 ]]; then
  UNIQ_ISSUES="$(printf '%s\n' "${ISSUE_IDS[@]}" | uniq_terms | head -5 | tr '\n' ' ')"
  # shellcheck disable=SC2086
  run_search "search by issue/ticket id (no date cap)" --repo "${REPO}" --limit 20 ${UNIQ_ISSUES}
fi

echo "## note: this script lists candidates; the agent scores strong vs weak."
