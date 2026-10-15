#!/usr/bin/env bash
# Interpreter discovery shim for migration-replay smoke / gates.
#
# Prefers the phase-pinned 3.15.0rc2 prefix and Homebrew 3.14 without requiring
# pyenv. Env overrides always win.
#
# Source (exports only):
#   source scripts/migration_replay/python_env.sh
#
# Exec with a pinned minor on PATH / PYTHON:
#   scripts/migration_replay/python_env.sh --python 3.15 -- \
#     scripts/run-profiling-tests --python 3.15 --quick
#
# When sourced: export only (do not touch caller's set -e).
# When executed: strict mode + optional --python pin + exec.
_MR_SOURCED=0
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  _MR_SOURCED=1
else
  set -euo pipefail
fi

PY315RC2="${PY315RC2:-${HOME}/.local/py315rc2}"
PYTHON314="${PYTHON314:-/opt/homebrew/bin/python3.14}"
PY315_BIN="${PY315RC2}/bin/python3.15"

export PY315RC2 PYTHON314

# Prepend known prefixes so command -v python3.14 / python3.15 works.
_MR_PATH_PREFIX=""
if [[ -d "${PY315RC2}/bin" ]]; then
  _MR_PATH_PREFIX="${PY315RC2}/bin"
fi
if [[ -d /opt/homebrew/bin ]]; then
  if [[ -n "${_MR_PATH_PREFIX}" ]]; then
    _MR_PATH_PREFIX="${_MR_PATH_PREFIX}:/opt/homebrew/bin"
  else
    _MR_PATH_PREFIX="/opt/homebrew/bin"
  fi
fi
if [[ -n "${_MR_PATH_PREFIX}" ]]; then
  export PATH="${_MR_PATH_PREFIX}:${PATH}"
fi

_mr_pin_python() {
  local version="$1"
  case "$version" in
    3.15)
      if [[ ! -x "${PY315_BIN}" ]]; then
        echo "ERROR: PY315RC2 python missing: ${PY315_BIN}" >&2
        echo "Build/install 3.15.0rc2 into ${PY315RC2} or set PY315RC2." >&2
        return 1
      fi
      export PYTHON="${PY315_BIN}"
      ;;
    3.14)
      if [[ ! -x "${PYTHON314}" ]]; then
        echo "ERROR: PYTHON314 missing: ${PYTHON314}" >&2
        echo "Install Homebrew python@3.14 or set PYTHON314." >&2
        return 1
      fi
      export PYTHON="${PYTHON314}"
      ;;
    *)
      echo "ERROR: python_env.sh --python supports 3.14 or 3.15 (got ${version})" >&2
      return 2
      ;;
  esac
}

if [[ "${_MR_SOURCED}" -eq 1 ]]; then
  unset _MR_SOURCED _MR_PATH_PREFIX
  return 0
fi
unset _MR_SOURCED

USAGE='Usage: python_env.sh [--python 3.14|3.15] -- COMMAND [ARGS...]
       source python_env.sh'

PIN_VERSION=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      PIN_VERSION="$2"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      echo "$USAGE"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      echo "$USAGE" >&2
      exit 2
      ;;
  esac
done

if [[ $# -eq 0 ]]; then
  echo "$USAGE" >&2
  exit 2
fi

if [[ -n "$PIN_VERSION" ]]; then
  _mr_pin_python "$PIN_VERSION"
fi

exec "$@"
