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

  echo "[uv] uv not found, installing..." >&2
  if [[ "$OSTYPE" == "darwin"* ]] || command -v brew >/dev/null 2>&1; then
    echo "[uv] Installing via Homebrew..." >&2
    brew install uv >&2
  else
    echo "[uv] Installing via installer script..." >&2
    curl -LsSf https://astral.sh/uv/install.sh | sh >&2
  fi

  for path in "${CANDIDATE_PATHS[@]}"; do
    if [[ -x "$path/uv" ]]; then
      export PATH="$path:$PATH"
      return 0
    fi
  done

  echo "[uv] ERROR: uv installation failed or not found in PATH." >&2
  return 1
}
