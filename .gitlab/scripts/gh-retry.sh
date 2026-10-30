# Retry helper for gh calls in GitLab CI jobs. Source this file from sh or bash:
#
#   . .gitlab/scripts/gh-retry.sh
#
# GitHub can return a transient error, such as HTTP 500 or 502, for any API call.
# Without a retry, one such error fails the job. The file is POSIX sh, because
# some jobs run in images that do not have bash.

# Runs gh with the given arguments. If the call fails, tries it again, up to
# GH_RETRY_ATTEMPTS attempts (default 5). The first delay is GH_RETRY_DELAY
# seconds (default 5) and doubles after each failure. Messages go to stderr, so
# the caller can capture stdout.
#
# Run "gh run watch" without --exit-status, so that only an API error makes it
# fail. Then check the run conclusion, so that a failed run is not retried.
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
