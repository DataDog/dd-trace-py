#!/usr/bin/env bash
# Unit tests for resolve-base-branch.sh.
#
# Self-contained: stubs `git` and `curl` on PATH so the tests are hermetic (no
# network, no real repo state) and need no extra tooling beyond bash + jq. Run:
#
#   .gitlab/scripts/resolve-base-branch.test.sh
#
# Exits non-zero if any case fails, so it can be wired straight into CI.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${SCRIPT_DIR}/resolve-base-branch.sh"

if [ ! -x "${TARGET}" ]; then
  echo "cannot find executable script under test: ${TARGET}" >&2
  exit 1
fi

TMPROOT="$(mktemp -d)"
BIN="${TMPROOT}/bin"
mkdir -p "${BIN}"
trap 'rm -rf "${TMPROOT}"' EXIT

# --- stub: git -------------------------------------------------------------
# Only `git ls-remote --exit-code --heads origin refs/heads/<b>` is used.
# Succeed iff <b> is listed in FAKE_EXISTING_BRANCHES (colon-separated).
cat > "${BIN}/git" <<'STUB'
#!/usr/bin/env bash
if [ "${1:-}" = "ls-remote" ]; then
  ref="${!#}"                       # last arg: refs/heads/<branch>
  branch="${ref#refs/heads/}"
  IFS=':' read -ra existing <<< "${FAKE_EXISTING_BRANCHES:-}"
  for b in "${existing[@]}"; do
    if [ "${b}" = "${branch}" ]; then
      echo "deadbeef	refs/heads/${branch}"
      exit 0
    fi
  done
  exit 2
fi
exit 0
STUB
chmod +x "${BIN}/git"

# --- stub: curl ------------------------------------------------------------
# Records that it was invoked (so we can assert the fast path skips it) and
# emits FAKE_CURL_BODY as the GitHub API response.
cat > "${BIN}/curl" <<'STUB'
#!/usr/bin/env bash
if [ -n "${FAKE_CURL_MARKER:-}" ]; then : > "${FAKE_CURL_MARKER}"; fi
printf '%s' "${FAKE_CURL_BODY:-[]}"
exit 0
STUB
chmod +x "${BIN}/curl"

# --- stub: dd-octo-sts -----------------------------------------------------
# Records that it was invoked (to assert lazy minting happens only on the API
# path) and prints a fake token on `dd-octo-sts token ...`.
cat > "${BIN}/dd-octo-sts" <<'STUB'
#!/usr/bin/env bash
if [ -n "${FAKE_OCTOSTS_MARKER:-}" ]; then : > "${FAKE_OCTOSTS_MARKER}"; fi
if [ "${1:-}" = "token" ]; then echo "fake-token"; fi
exit 0
STUB
chmod +x "${BIN}/dd-octo-sts"

export PATH="${BIN}:${PATH}"

# Make sure the real environment doesn't leak into cases that expect these
# unset; each case sets what it needs via an inline env prefix.
unset CI_COMMIT_REF_NAME CI_MERGE_REQUEST_TARGET_BRANCH_NAME GITHUB_BASE_REF GH_TOKEN 2>/dev/null || true

pass=0
fail=0

check() {
  # check <got> <expected> <description>
  local got="$1" expected="$2" desc="$3"
  if [ "${got}" = "${expected}" ]; then
    printf 'ok   - %s (=> %s)\n' "${desc}" "${got}"
    pass=$((pass + 1))
  else
    printf 'FAIL - %s: expected %q, got %q\n' "${desc}" "${expected}" "${got}"
    fail=$((fail + 1))
  fi
}

assert_present() {
  # assert_present <path> <description>
  if [ -e "$1" ]; then
    printf 'ok   - %s\n' "$2"
    pass=$((pass + 1))
  else
    printf 'FAIL - %s (%s missing)\n' "$2" "$1"
    fail=$((fail + 1))
  fi
}

assert_absent() {
  # assert_absent <path> <description>
  if [ ! -e "$1" ]; then
    printf 'ok   - %s\n' "$2"
    pass=$((pass + 1))
  else
    printf 'FAIL - %s (%s exists)\n' "$2" "$1"
    fail=$((fail + 1))
  fi
}

# 1. main compares against itself.
check "$(bash "${TARGET}" main 2>/dev/null)" "main" "ref=main -> main"

# 2. release tag -> derived release branch (branch exists).
check "$(FAKE_EXISTING_BRANCHES="4.12" bash "${TARGET}" v4.12.3 2>/dev/null)" \
  "4.12" "release tag v4.12.3 -> 4.12"

# 3. release tag whose release branch does not exist -> main.
check "$(FAKE_EXISTING_BRANCHES="" bash "${TARGET}" v9.9.9 2>/dev/null)" \
  "main" "release tag with missing branch -> main"

# 4. release branch compares against itself.
check "$(bash "${TARGET}" 4.12 2>/dev/null)" "4.12" "ref=4.12 -> 4.12"

# 5. merge-queue ref -> extracted target branch.
check "$(bash "${TARGET}" "gh-readonly-queue/4.12/pr-123-abc" 2>/dev/null)" \
  "4.12" "merge-queue ref -> 4.12"

# 6. feature branch with GitLab MR target var -> fast path, no curl, no token.
marker="${TMPROOT}/curl_called_6"; rm -f "${marker}"
octo="${TMPROOT}/octo_called_6"; rm -f "${octo}"
got="$(CI_MERGE_REQUEST_TARGET_BRANCH_NAME="4.12" FAKE_CURL_MARKER="${marker}" \
  FAKE_OCTOSTS_MARKER="${octo}" bash "${TARGET}" my-feature 2>/dev/null)"
check "${got}" "4.12" "feature branch + CI_MERGE_REQUEST_TARGET_BRANCH_NAME -> 4.12"
assert_absent "${marker}" "fast path (MR var) skips the curl call"
assert_absent "${octo}" "fast path (MR var) skips dd-octo-sts token minting"

# 7. feature branch with GitHub Actions base ref -> fast path.
marker="${TMPROOT}/curl_called_7"; rm -f "${marker}"
got="$(GITHUB_BASE_REF="4.11" FAKE_CURL_MARKER="${marker}" \
  bash "${TARGET}" my-feature 2>/dev/null)"
check "${got}" "4.11" "feature branch + GITHUB_BASE_REF -> 4.11"
assert_absent "${marker}" "fast path (GITHUB_BASE_REF) skips the curl call"

# 8. feature branch, no CI target vars -> GitHub API lookup, token minted lazily.
if command -v jq > /dev/null 2>&1; then
  body='[{"base":{"ref":"4.10"}}]'
  octo="${TMPROOT}/octo_called_8"; rm -f "${octo}"
  check "$(FAKE_CURL_BODY="${body}" FAKE_OCTOSTS_MARKER="${octo}" \
    bash "${TARGET}" backport-x-to-4.10 2>/dev/null)" \
    "4.10" "feature branch, API returns base.ref -> 4.10"
  assert_present "${octo}" "API path mints a token via dd-octo-sts when GH_TOKEN unset"

  # 8b. GH_TOKEN pre-set -> API path must NOT mint a token.
  octo="${TMPROOT}/octo_called_8b"; rm -f "${octo}"
  check "$(GH_TOKEN="preset" FAKE_CURL_BODY="${body}" FAKE_OCTOSTS_MARKER="${octo}" \
    bash "${TARGET}" backport-x-to-4.10 2>/dev/null)" \
    "4.10" "feature branch, pre-set GH_TOKEN, API returns base.ref -> 4.10"
  assert_absent "${octo}" "pre-set GH_TOKEN skips dd-octo-sts token minting"

  # 9. feature branch, API returns no open PR -> main.
  check "$(FAKE_CURL_BODY="[]" bash "${TARGET}" orphan-branch 2>/dev/null)" \
    "main" "feature branch, no open PR -> main"
else
  printf 'skip - API-lookup cases (jq not installed)\n'
fi

# 10. no ref and CI_COMMIT_REF_NAME unset -> main.
check "$(bash "${TARGET}" 2>/dev/null)" "main" "no ref -> main"

echo
echo "passed: ${pass}, failed: ${fail}"
[ "${fail}" -eq 0 ]
