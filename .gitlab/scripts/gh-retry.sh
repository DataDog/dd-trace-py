# Retry helpers for gh calls in GitLab CI jobs. Source this file from sh or bash:
#
#   . .gitlab/scripts/gh-retry.sh
#
# GitHub can return a transient error, such as HTTP 500 or 502, for any API call.
# Without a retry, one such error fails the job.
#
# GH_RETRY_ATTEMPTS sets the maximum number of attempts (default 5).
# GH_RETRY_DELAY sets the first delay in seconds (default 5). The delay doubles
# after each failed attempt.
#
# The helpers write their messages to stderr, so the caller can capture stdout.
# The file is POSIX sh, because some jobs run in images that do not have bash.

# Runs gh with the given arguments. If the call fails, tries it again.
# Use it only for calls that are safe to repeat, for example reads. Do not use it
# for "gh workflow run": a dispatch that fails can start a run all the same.
gh_retry() {
  _gh_retry_attempt=1
  _gh_retry_delay="${GH_RETRY_DELAY:-5}"
  while :; do
    gh "$@" && return 0
    _gh_retry_status=$?
    if [ "${_gh_retry_attempt}" -ge "${GH_RETRY_ATTEMPTS:-5}" ]; then
      echo "error: gh $1 $2 failed after ${_gh_retry_attempt} attempts" >&2
      return "${_gh_retry_status}"
    fi
    echo "warning: gh $1 $2 failed with status ${_gh_retry_status} (attempt ${_gh_retry_attempt}), retrying in ${_gh_retry_delay}s" >&2
    sleep "${_gh_retry_delay}"
    _gh_retry_attempt=$((_gh_retry_attempt + 1))
    _gh_retry_delay=$((_gh_retry_delay * 2))
  done
}

# Usage: gh_watch_run <repo> <run-id> [gh run watch flags]
# Watches a workflow run until it completes. gh run watch also fails on an API
# error while the run continues. In that case, watch the run again. When GitHub
# reports the run as completed, return the status of the last watch.
gh_watch_run() {
  _gh_watch_repo="$1"
  _gh_watch_run_id="$2"
  shift 2
  _gh_watch_attempt=1
  _gh_watch_delay="${GH_RETRY_DELAY:-5}"
  while :; do
    gh run watch "${_gh_watch_run_id}" --repo "${_gh_watch_repo}" "$@" && return 0
    _gh_watch_status=$?
    _gh_watch_run_status="$(gh_retry run view "${_gh_watch_run_id}" --repo "${_gh_watch_repo}" --json status --jq .status || true)"
    if [ "${_gh_watch_run_status}" = "completed" ]; then
      return "${_gh_watch_status}"
    fi
    if [ "${_gh_watch_attempt}" -ge "${GH_RETRY_ATTEMPTS:-5}" ]; then
      echo "error: could not watch run ${_gh_watch_run_id} in ${_gh_watch_repo} after ${_gh_watch_attempt} attempts" >&2
      return "${_gh_watch_status}"
    fi
    echo "warning: watch of run ${_gh_watch_run_id} failed before the run completed (attempt ${_gh_watch_attempt}), retrying in ${_gh_watch_delay}s" >&2
    sleep "${_gh_watch_delay}"
    _gh_watch_attempt=$((_gh_watch_attempt + 1))
    _gh_watch_delay=$((_gh_watch_delay * 2))
  done
}
