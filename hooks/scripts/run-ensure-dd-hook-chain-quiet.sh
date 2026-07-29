#!/usr/bin/env bash
#
# Shared entrypoint for post-checkout and post-merge ensure-dd-hook-chain hooks.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/ensure-dd-hook-chain.sh" --quiet
