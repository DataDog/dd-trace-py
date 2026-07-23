#!/usr/bin/env bash
# Rebuild the 14-PR stack off origin/main with correct incremental commits.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

git fetch origin

GAB="Gabriele N. Tornetta <gabriele.tornetta@datadoghq.com>"
SRC=gab/315-monitoring-multiplexer

rebuild_layer() {
  local branch=$1
  local parent=$2
  local author_kind=$3
  local msg=$4
  local src=${5:-$branch}

  echo "==> ${branch} (from ${src}, parent ${parent})"
  git checkout -B _stack-fix "${parent}"
  files=()
  while IFS= read -r f; do files+=("$f"); done < <(git diff --name-only "${parent}" "origin/${src}")
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "No file diff for ${src}; skipping commit" >&2
    return 1
  fi
  git checkout "origin/${src}" -- "${files[@]}"
  if [[ "$author_kind" == gab ]]; then
    git -c commit.gpgsign=false commit --author="$GAB" -m "$msg"
  else
    git commit -m "$msg"
  fi
  git branch -f "$branch" HEAD
}

git checkout -B _stack-fix origin/main

# PR1
git checkout origin/gab/315-monitoring-multiplexer -- \
  ddtrace/internal/monitoring.py tests/internal/test_monitoring.py
git -c commit.gpgsign=false commit --author="$GAB" -m "feat(internal): add sys.monitoring multiplexer for Python 3.15

Introduces ddtrace.internal.monitoring for Python 3.15+.

Part of the #17849 split (PR 1/14)."
git branch -f gab/315-monitoring-multiplexer HEAD

# PR2
rebuild_layer chore/315-wrapping-context gab/315-monitoring-multiplexer gab \
  "chore(wrapping): add Python 3.15 wrapping context support

Part of the #17849 split (PR 2/14)." chore/315-wrapping-context
git branch -f gab/315-wrapping-context HEAD

rebuild_layer vlad/315-ci-matrix chore/315-wrapping-context vlad \
  "ci(py3.15): add 3.15 to riot matrix with gated suites

Part of the #17849 split (PR 3/14)."
rebuild_layer vlad/315-ci-autoregen-lockfiles vlad/315-ci-matrix vlad \
  "ci: auto-commit regenerated riot lockfiles on PR branches

Part of the #17849 split (PR 4/14)."
rebuild_layer vlad/315-official-support vlad/315-ci-autoregen-lockfiles vlad \
  "chore(py3.15): declare official 3.15 support in packaging

Part of the #17849 split (PR 5/14)."
rebuild_layer vlad/315-peripheral-compat vlad/315-official-support vlad \
  "fix(py3.15): peripheral compat for profiling, logging, and appsec

Part of the #17849 split (PR 6/14)."
rebuild_layer vlad/profiling-native-test-install-subdir vlad/315-peripheral-compat vlad \
  "feat(profiling): add INSTALL_SUBDIR keyword to dd_wrapper_add_test

Part of the profiling stack (PR 7/14)."
rebuild_layer vlad/ddtracepy-315-profiling-native vlad/profiling-native-test-install-subdir vlad \
  "chore(profiling): native C++/Rust py3.15 ABI support

Part of the profiling stack (PR 8/14)."
rebuild_layer vlad/ddtracepy-315-profiling-collectors vlad/ddtracepy-315-profiling-native vlad \
  "chore(profiling): update Python profiling collectors for py3.15

Part of the profiling stack (PR 9/14)."
rebuild_layer vlad/ddtracepy-315-profiling-only vlad/ddtracepy-315-profiling-collectors vlad \
  "ci(profiling): wire py3.15 into build matrix, riotfile, and CI

Part of the profiling stack (PR 10/14)."

# PR11: squash 3 asyncio commits into one layer diff
files=()
while IFS= read -r f; do files+=("$f"); done < <(git diff --name-only vlad/ddtracepy-315-profiling-only origin/vlad/ddtracepy-315-profiling-asyncio-monitoring)
git checkout -B _stack-fix vlad/ddtracepy-315-profiling-only
git checkout origin/vlad/ddtracepy-315-profiling-asyncio-monitoring -- "${files[@]}"
git commit -m "refactor(profiling): sys.monitoring asyncio path for py3.15

Part of the profiling stack (PR 11/14)."
git branch -f vlad/ddtracepy-315-profiling-asyncio-monitoring HEAD

rebuild_layer vlad/315-profiling-dev-tooling vlad/ddtracepy-315-profiling-asyncio-monitoring vlad \
  "docs(profiling): add py3.15 dev tooling and CPython upgrade runbook

Part of the post-stack follow-ups (PR 12/14)."

# PR12 tooling scripts (from origin dev-tooling branch)
git checkout origin/vlad/315-profiling-dev-tooling -- scripts/py315-stack/ 2>/dev/null || true
if [[ -d scripts/py315-stack ]]; then
  git add scripts/py315-stack/
  if ! git diff --cached --quiet; then
    git commit -m "chore(py315): stack PR navigation scripts and bodies

Part of the post-stack follow-ups (PR 12/14)."
  fi
fi
git branch -f vlad/315-profiling-dev-tooling HEAD

rebuild_layer vlad/315-lib-injection-ssi vlad/315-profiling-dev-tooling vlad \
  "chore(py-315): enable lib-injection SSI for Python 3.15

Part of the post-stack follow-ups (PR 13/14)."
rebuild_layer vlad/315-profiling-release-note vlad/315-lib-injection-ssi vlad \
  "docs(releasenotes): add profiling Python 3.15 support note

Part of the post-stack follow-ups (PR 14/14)."

echo ""
echo "Stack rebuilt. Branch tips:"
for b in gab/315-monitoring-multiplexer chore/315-wrapping-context vlad/315-ci-matrix \
  vlad/315-ci-autoregen-lockfiles vlad/315-official-support vlad/315-peripheral-compat \
  vlad/profiling-native-test-install-subdir vlad/ddtracepy-315-profiling-native \
  vlad/ddtracepy-315-profiling-collectors vlad/ddtracepy-315-profiling-only \
  vlad/ddtracepy-315-profiling-asyncio-monitoring vlad/315-profiling-dev-tooling \
  vlad/315-lib-injection-ssi vlad/315-profiling-release-note; do
  inc=$(git rev-list --count "origin/${b}^..${b}" 2>/dev/null || git rev-list --count "${b}^..${b}")
  total=$(git rev-list --count origin/main.."${b}")
  echo "  ${b}: +${inc} commit(s), ${total} total vs main"
done
