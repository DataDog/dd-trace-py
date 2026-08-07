#!/usr/bin/env sh
# Optional integration checks for the DD global git hook chain.
#
# Skipped by default (CI and non-DD laptops). Run manually:
#   DD_HOOK_CHAIN_INTEGRATION=1 sh hooks/tests/test-hook-chain-integration.sh
# Or use the richer verifier:
#   hooks/scripts/verify-dd-hook-chain.sh [--secrets]

set -eu

if [ "${DD_HOOK_CHAIN_INTEGRATION:-0}" != "1" ]; then
  echo "test-hook-chain-integration: skipped (set DD_HOOK_CHAIN_INTEGRATION=1 on a DD laptop)"
  exit 0
fi

VERIFY_SCRIPT="$(cd "$(dirname "$0")/../scripts" && pwd)/verify-dd-hook-chain.sh"
if [ ! -x "$VERIFY_SCRIPT" ]; then
  echo "test-hook-chain-integration: missing $VERIFY_SCRIPT" >&2
  exit 1
fi

exec sh "$VERIFY_SCRIPT"
