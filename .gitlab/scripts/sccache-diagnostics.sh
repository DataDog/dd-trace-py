#!/usr/bin/env bash
# Print the sccache server state on this machine. Best effort: never fails the job.
#
# The macOS amd64 runner reuses its Macs and can run several jobs on one of them at once,
# and they may share one local sccache server. This shows which server a job talks to,
# how old it is, and how busy the machine is.

port="${SCCACHE_SERVER_PORT:-4226}"

echo "=== sccache diagnostics ($1) ==="
echo "host=$(hostname) concurrent_id=${CI_CONCURRENT_ID:-} job=${CI_JOB_ID:-} dir=${CI_PROJECT_DIR:-}"
uptime || true
echo "--- sccache processes (pid ppid pgid elapsed command) ---"
ps -axo pid=,ppid=,pgid=,etime=,command= | awk '$5 ~ /(^|\/)sccache$/ { found = 1; print } END { if (!found) print "none" }'
echo "--- listening on port ${port} ---"
lsof -nP -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || echo "none"
echo "--- open connections to port ${port}: $(lsof -nP -iTCP:"${port}" -sTCP:ESTABLISHED 2>/dev/null | grep -c ESTABLISHED) ---"
echo "--- wheel builds running on this machine (this job included) ---"
ps -axo pid=,etime=,command= | grep -E '[b]uild-wheel\.sh|[s]etup\.py' || echo "none"
exit 0
