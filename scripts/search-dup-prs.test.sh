#!/usr/bin/env bash
# Hermetic tests for search-dup-prs.sh skip and profiling-scope paths.
# Uses a temp git repo and a gh stub. No network. Exits non-zero on failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${SCRIPT_DIR}/search-dup-prs.sh"

if [[ ! -x "${TARGET}" ]]; then
  echo "cannot find executable script under test: ${TARGET}" >&2
  exit 1
fi

TMPROOT="$(mktemp -d)"
trap 'rm -rf "${TMPROOT}"' EXIT

pass=0
fail=0

check_rc() {
  local got="$1" expected="$2" desc="$3"
  if [[ "${got}" == "${expected}" ]]; then
    printf 'ok   - %s (rc=%s)\n' "${desc}" "${got}"
    pass=$((pass + 1))
  else
    printf 'FAIL - %s: expected rc %s, got %s\n' "${desc}" "${expected}" "${got}"
    fail=$((fail + 1))
  fi
}

check_contains() {
  local haystack="$1" needle="$2" desc="$3"
  if [[ "${haystack}" == *"${needle}"* ]]; then
    printf 'ok   - %s\n' "${desc}"
    pass=$((pass + 1))
  else
    printf 'FAIL - %s: missing %q in:\n%s\n' "${desc}" "${needle}" "${haystack}"
    fail=$((fail + 1))
  fi
}

check_not_contains() {
  local haystack="$1" needle="$2" desc="$3"
  if [[ "${haystack}" != *"${needle}"* ]]; then
    printf 'ok   - %s\n' "${desc}"
    pass=$((pass + 1))
  else
    printf 'FAIL - %s: unexpectedly found %q\n' "${desc}" "${needle}"
    fail=$((fail + 1))
  fi
}

init_repo() {
  local dir="$1"
  local origin_url="$2"
  mkdir -p "${dir}"
  git -C "${dir}" init -b main >/dev/null
  git -C "${dir}" remote add origin "${origin_url}"
  git -C "${dir}" config user.name Test
  git -C "${dir}" config user.email test@example.com
  printf 'base\n' > "${dir}/README"
  git -C "${dir}" add README
  git -C "${dir}" -c commit.gpgsign=false commit -m 'base' >/dev/null
  git -C "${dir}" update-ref refs/remotes/origin/main HEAD
}

# 1. Not a git repository.
NOT_GIT="${TMPROOT}/not-git"
mkdir -p "${NOT_GIT}"
set +e
out="$(cd "${NOT_GIT}" && "${TARGET}" 2>&1)"
rc=$?
set -e
check_rc "${rc}" 1 "not a git repo exits 1"
check_contains "${out}" "not a git repository" "not a git repo error text"

# 2. Other GitHub repo: skip, no search.
OTHER="${TMPROOT}/other"
init_repo "${OTHER}" "git@github.com:example/other.git"
set +e
out="$(cd "${OTHER}" && "${TARGET}" 2>&1)"
rc=$?
set -e
check_rc "${rc}" 0 "other repo exits 0"
check_contains "${out}" "## scope: skip" "other repo skips"
check_not_contains "${out}" "## scope: profiling" "other repo does not claim profiling"

# 3. dd-trace-py, no profiling files: skip.
NOPROF="${TMPROOT}/noprof"
init_repo "${NOPROF}" "git@github.com:DataDog/dd-trace-py.git"
mkdir -p "${NOPROF}/docs"
printf 'note\n' > "${NOPROF}/docs/note.md"
git -C "${NOPROF}" add docs/note.md
git -C "${NOPROF}" -c commit.gpgsign=false commit -m 'docs' >/dev/null
set +e
out="$(cd "${NOPROF}" && "${TARGET}" 2>&1)"
rc=$?
set -e
check_rc "${rc}" 0 "non-profiling dd-trace-py exits 0"
check_contains "${out}" "## scope: skip (not Python profiling" "non-profiling skip"
check_not_contains "${out}" "## open (includes drafts)" "non-profiling does not list PRs"

# 4. Shared suitespec is not profiling.
SUITE="${TMPROOT}/suite"
init_repo "${SUITE}" "git@github.com:DataDog/dd-trace-py.git"
mkdir -p "${SUITE}/tests"
printf 'suites:\n' > "${SUITE}/tests/suitespec.yml"
git -C "${SUITE}" add tests/suitespec.yml
git -C "${SUITE}" -c commit.gpgsign=false commit -m 'suitespec' >/dev/null
set +e
out="$(cd "${SUITE}" && "${TARGET}" 2>&1)"
rc=$?
set -e
check_rc "${rc}" 0 "root suitespec exits 0"
check_contains "${out}" "## scope: skip (not Python profiling" "root suitespec is not profiling"

# 5. Profiling path: scope profiling, search uses gh stub (no network).
PROF="${TMPROOT}/prof"
init_repo "${PROF}" "git@github.com:DataDog/dd-trace-py.git"
mkdir -p "${PROF}/ddtrace/profiling"
printf 'x\n' > "${PROF}/ddtrace/profiling/foo.py"
git -C "${PROF}" add ddtrace/profiling/foo.py
git -C "${PROF}" -c commit.gpgsign=false commit -m 'profiling' >/dev/null

BIN="${TMPROOT}/bin"
mkdir -p "${BIN}"
cat > "${BIN}/gh" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  repo\ view*)
    echo main
    ;;
  pr\ list*)
    echo '[]'
    ;;
  search\ prs*)
    echo '[]'
    ;;
  *)
    echo "unexpected gh $*" >&2
    exit 1
    ;;
esac
STUB
chmod +x "${BIN}/gh"

set +e
out="$(cd "${PROF}" && PATH="${BIN}:${PATH}" "${TARGET}" heap-live 2>&1)"
rc=$?
set -e
check_rc "${rc}" 0 "profiling path exits 0 with gh stub"
check_contains "${out}" "## scope: profiling" "profiling path is in scope"
check_contains "${out}" "ddtrace/profiling/foo.py" "lists the profiling file"
check_contains "${out}" "## extra-terms: heap live" "passes extra keywords"
check_contains "${out}" "## open (includes drafts)" "lists open PRs when in scope"

if [[ "${fail}" -gt 0 ]]; then
  printf '\n%d failed, %d passed\n' "${fail}" "${pass}"
  exit 1
fi
printf '\n%d passed\n' "${pass}"
exit 0
