#!/usr/bin/env bash
#
# Manual verifier for the Datadog global git hook chain on DD laptops.
#
# Usage:
#   hooks/scripts/verify-dd-hook-chain.sh            # wiring + doctor
#   hooks/scripts/verify-dd-hook-chain.sh --secrets  # also probe secrets scan
#
# Requires /usr/local/dd/global_hooks (not available in CI).

set -euo pipefail

RUN_SECRETS=0
if [ "${1:-}" = "--secrets" ]; then
  RUN_SECRETS=1
fi

GLOBAL_HOOKS="/usr/local/dd/global_hooks"
DD_GIT_HOOKS="$GLOBAL_HOOKS/dd-git-hooks"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ENSURE_SCRIPT="$REPO_ROOT/hooks/scripts/ensure-dd-hook-chain.sh"

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; FAILURES=$((FAILURES + 1)); }
skip() { echo "SKIP: $*"; }

FAILURES=0
TMPDIR_VERIFY=$(mktemp -d)
trap 'rm -rf "$TMPDIR_VERIFY"' EXIT

if [ ! -x "$DD_GIT_HOOKS" ]; then
  echo "dd-git-hooks is not installed ($DD_GIT_HOOKS)." >&2
  echo "This script is for Datadog laptops only." >&2
  exit 1
fi

echo "=== dd-git-hooks doctor ==="
if ! "$DD_GIT_HOOKS" -doctor \
  -repo="$REPO_ROOT" \
  -repo_name=dd-trace-py \
  -org=DataDog \
  -hooks-path="$GLOBAL_HOOKS" \
  -hooktype=pre-commit; then
  fail "dd-git-hooks doctor reported problems"
else
  pass "dd-git-hooks doctor"
fi

setup_verify_repo() {
  rm -rf "$TMPDIR_VERIFY/repo"
  mkdir -p "$TMPDIR_VERIFY/repo"
  cat >"$TMPDIR_VERIFY/global.gitconfig" <<EOF
[core]
	hooksPath = $GLOBAL_HOOKS
EOF
  (
    cd "$TMPDIR_VERIFY/repo"
    GIT_CONFIG_GLOBAL="$TMPDIR_VERIFY/global.gitconfig" GIT_CONFIG_SYSTEM=/dev/null \
      git init -q
    GIT_CONFIG_GLOBAL="$TMPDIR_VERIFY/global.gitconfig" GIT_CONFIG_SYSTEM=/dev/null \
      git config user.email "hook-verify@test"
    GIT_CONFIG_GLOBAL="$TMPDIR_VERIFY/global.gitconfig" GIT_CONFIG_SYSTEM=/dev/null \
      git config user.name "hook-verify"
    GIT_CONFIG_GLOBAL="$TMPDIR_VERIFY/global.gitconfig" GIT_CONFIG_SYSTEM=/dev/null \
      git remote add origin "git@github.com:DataDog/dd-trace-py.git"
    GIT_CONFIG_GLOBAL="$TMPDIR_VERIFY/global.gitconfig" GIT_CONFIG_SYSTEM=/dev/null \
      git commit --allow-empty -q -m init
  )
}

git_verify() {
  GIT_CONFIG_GLOBAL="$TMPDIR_VERIFY/global.gitconfig" GIT_CONFIG_SYSTEM=/dev/null "$@"
}

marker_file() {
  ls /tmp/dd-hook-verify-local-marker.* 2>/dev/null | head -1 || true
}

echo ""
echo "=== local hooksPath bypass vs global chain ==="

setup_verify_repo
(
  cd "$TMPDIR_VERIFY/repo"
  mkdir -p .git/hooks
  cat >.git/hooks/pre-commit <<'EOF'
#!/usr/bin/env bash
echo "local" >"/tmp/dd-hook-verify-local-marker.$$"
exit 0
EOF
  chmod +x .git/hooks/pre-commit
  git_verify git config --local core.hooksPath .git/hooks
  rm -f /tmp/dd-hook-verify-local-marker.*
  git_verify git commit --allow-empty -m bypass-test >/dev/null 2>&1 || true
  if [ -n "$(marker_file)" ]; then
    pass "local core.hooksPath=.git/hooks runs repo hook directly (global chain skipped)"
  else
    fail "expected local hook marker when core.hooksPath=.git/hooks is set"
  fi
  rm -f /tmp/dd-hook-verify-local-marker.*
)

setup_verify_repo
if [ ! -x "$ENSURE_SCRIPT" ]; then
  skip "ensure-dd-hook-chain repair check (not in this checkout)"
else
  (
    cd "$TMPDIR_VERIFY/repo"
    git_verify git config --local core.hooksPath .git/hooks
    GIT_CONFIG_GLOBAL="$TMPDIR_VERIFY/global.gitconfig" GIT_CONFIG_SYSTEM=/dev/null \
      DD_HOOK_TELEMETRY_FILE= DD_HOOK_TELEMETRY= \
      "$ENSURE_SCRIPT" --quiet
    if [ -z "$(git_verify git config --local --get core.hooksPath 2>/dev/null || true)" ]; then
      pass "ensure-dd-hook-chain removes local .git/hooks override"
    else
      fail "ensure-dd-hook-chain should unset local core.hooksPath=.git/hooks"
    fi
  )
fi

setup_verify_repo
(
  cd "$TMPDIR_VERIFY/repo"
  out=$(DD_GIT_HOOKS_DEBUG=1 git_verify git -c core.hooksPath="$GLOBAL_HOOKS" commit --allow-empty -m global-chain-test 2>&1) || true
  if echo "$out" | grep -Fq "dd-git-hooks"; then
    pass "global core.hooksPath invokes dd-git-hooks before commit"
  else
    fail "expected dd-git-hooks in global pre-commit output"
  fi
)

if [ "$RUN_SECRETS" -eq 1 ]; then
  echo ""
  echo "=== secrets scan probe (staged fake credential) ==="
  if [ -z "${DD_HOOK_VERIFY_EXAMPLE_SECRET:-}" ]; then
    skip "secrets probe skipped (set DD_HOOK_VERIFY_EXAMPLE_SECRET to a test credential)"
  else
    setup_verify_repo
    (
      cd "$TMPDIR_VERIFY/repo"
      printf '%s\n' "$DD_HOOK_VERIFY_EXAMPLE_SECRET" > leak.py
      git_verify git add leak.py
      if DD_GIT_HOOKS_DEBUG=1 git_verify git -c core.hooksPath="$GLOBAL_HOOKS" commit -m secret-probe 2>&1; then
        skip "commit was not blocked — dd-git-hooks may only block verified secrets, or the local scanner needs attention (see doctor)"
      else
        pass "dd-git-hooks blocked commit with staged fake credential"
      fi
    )
  fi
else
  echo ""
  echo "Secrets probe not run. To try a staged test credential:"
  echo "  DD_HOOK_VERIFY_EXAMPLE_SECRET='...' $0 --secrets"
fi

echo ""
if [ "$FAILURES" -eq 0 ]; then
  echo "verify-dd-hook-chain: all checks passed"
  exit 0
fi

echo "verify-dd-hook-chain: $FAILURES check(s) failed" >&2
exit 1
