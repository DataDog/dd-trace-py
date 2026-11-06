#!/usr/bin/env bash
# usage: run.sh <baseline|apm-only|apm+dsm> [slug]
#
# Runs bench.py in this folder's own uv venv, against the ddtrace built from the repo checkout
# containing this script (so it works from any worktree). Env overrides: see README.md.
set -euo pipefail
cd "$(dirname "$0")"

SCENARIO=${1:?usage: run.sh <baseline|apm-only|apm+dsm> [slug]}
SLUG=${2:-base}
NAME=${SCENARIO//+/plus}
OUT_DIR=${OUT_DIR:-$PWD/out}
mkdir -p "$OUT_DIR"

export KAFKA_TOPIC=${KAFKA_TOPIC:-benchmark-topic-python-$NAME-$SLUG}
export TIMEIT_OUTPUT=${TIMEIT_OUTPUT:-$OUT_DIR/python-$SLUG.$NAME.json}
export PROFILE_OUTPUT=${PROFILE_OUTPUT:-$OUT_DIR/python-$SLUG.$NAME}
export DD_TRACE_STARTUP_LOGS=${DD_TRACE_STARTUP_LOGS:-false}
# Must accept payloads: a dead port adds connection-error overhead to the tracer scenarios (see README).
export DD_TRACE_AGENT_URL=${DD_TRACE_AGENT_URL:-http://127.0.0.1:18127}

export UV_PYTHON=${UV_PYTHON:-3.14}
# First sync builds ddtrace's native extensions in-tree (several minutes); later runs are no-ops.
: > /tmp/empty-gitconfig  # ignore the global git config: an insteadOf rewrite of github.com to SSH breaks cargo's libdatadog fetch
GIT_CONFIG_GLOBAL=/tmp/empty-gitconfig CARGO_NET_GIT_FETCH_WITH_CLI=true uv sync --quiet

case $SCENARIO in
  baseline) unset DD_DATA_STREAMS_ENABLED; exec uv run --no-sync python bench.py ;;
  apm-only) DD_DATA_STREAMS_ENABLED=false exec uv run --no-sync ddtrace-run python bench.py ;;
  apm+dsm)  DD_DATA_STREAMS_ENABLED=true exec uv run --no-sync ddtrace-run python bench.py ;;
  *) echo "unknown scenario: $SCENARIO" >&2; exit 2 ;;
esac
