#!/usr/bin/env bash
# Shared helper: ensure uv is on PATH, installing it if necessary.
# Source this file; do not execute it directly.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/_uv-bootstrap.sh"
#   ensure_uv || exit 1

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi

  local CANDIDATE_PATHS=(
    "$HOME/.local/bin"
    "$HOME/.cargo/bin"
  )

  for path in "${CANDIDATE_PATHS[@]}"; do
    if [[ -x "$path/uv" ]]; then
      export PATH="$path:$PATH"
      return 0
    fi
  done

  echo "[uv] uv not found, installing..."
  if [[ "$OSTYPE" == "darwin"* ]] || command -v brew >/dev/null 2>&1; then
    echo "[uv] Installing via Homebrew..."
    brew install uv
  else
    echo "[uv] Installing via installer script..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi

  for path in "${CANDIDATE_PATHS[@]}"; do
    if [[ -x "$path/uv" ]]; then
      export PATH="$path:$PATH"
      return 0
    fi
  done

  echo "[uv] ERROR: uv installation failed or not found in PATH."
  return 1
}
