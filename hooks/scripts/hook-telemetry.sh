#!/usr/bin/env bash
#
# Best-effort DogStatsd telemetry for repo git hooks on Datadog laptops.
# Never fails the caller; metrics are optional and fire-and-forget.
#
# Metrics (count):
#   dd.trace.repo_hooks.autohook.executions
#   dd.trace.repo_hooks.hook_chain.bypass
#   dd.trace.repo_hooks.hook_chain.repair
#
# Enable when global core.hooksPath is /usr/local/dd/global_hooks, or when
# DD_HOOK_TELEMETRY=1. Tests may set DD_HOOK_TELEMETRY_FILE to capture payloads.

# AIDEV-NOTE: hook_chain_fix tags attribute repairs to ensure-dd-hook-chain v1 (PR #19326).

readonly DD_HOOK_CHAIN_FIX_TAG="ensure-dd-hook-chain-v1"

_dd_hook_telemetry_sanitize() {
  local value="${1:-unknown}"
  value="${value// /_}"
  value="${value//|/_}"
  value="${value//,/_}"
  value="${value//:/_}"
  printf '%s' "$value"
}

dd_hook_telemetry_enabled() {
  if [ "${DD_HOOK_TELEMETRY:-0}" = "1" ]; then
    return 0
  fi
  [ "$(git config --global --get core.hooksPath 2>/dev/null || true)" = "/usr/local/dd/global_hooks" ]
}

dd_hook_telemetry_username() {
  _dd_hook_telemetry_sanitize "${USER:-${LOGNAME:-unknown}}"
}

dd_hook_telemetry_repo_context() {
  local remote org repo_name git_dir
  git_dir="$(pwd)"
  remote="$(git remote get-url origin 2>/dev/null || true)"
  remote="${remote:-UNDEFINED}"
  org="$git_dir"
  repo_name="$git_dir"

  if [[ $remote =~ ^(https|git)(://|@)([^/:]+)[/:]([^/]+)/(.+)$ ]]; then
    org="${BASH_REMATCH[4]}"
    repo_name="${BASH_REMATCH[5]}"
    repo_name="${repo_name%.git}"
  fi

  DD_HOOK_TELEMETRY_GITHUB_ORG="$(_dd_hook_telemetry_sanitize "$org")"
  DD_HOOK_TELEMETRY_GITHUB_REPO="$(_dd_hook_telemetry_sanitize "$repo_name")"
  export DD_HOOK_TELEMETRY_GITHUB_ORG DD_HOOK_TELEMETRY_GITHUB_REPO
}

_dd_hook_telemetry_send() {
  local payload="$1"
  if [ -n "${DD_HOOK_TELEMETRY_FILE:-}" ]; then
    printf '%s\n' "$payload" >>"$DD_HOOK_TELEMETRY_FILE"
    return 0
  fi

  local host="${DD_DOGSTATSD_HOST:-127.0.0.1}"
  local port="${DD_DOGSTATSD_PORT:-8125}"
  if [ -n "${DD_DOGSTATSD_URL:-}" ]; then
    case "$DD_DOGSTATSD_URL" in
      udp://*)
        host="${DD_DOGSTATSD_URL#udp://}"
        host="${host%%:*}"
        port="${DD_DOGSTATSD_URL##*:}"
        ;;
    esac
  fi

  if command -v nc >/dev/null 2>&1; then
    printf '%s' "$payload" | nc -u -w1 "$host" "$port" 2>/dev/null || true
  fi
}

dd_hook_telemetry_count() {
  local metric="$1"
  shift

  dd_hook_telemetry_enabled || return 0
  dd_hook_telemetry_repo_context

  local tags=(
    "github_org:${DD_HOOK_TELEMETRY_GITHUB_ORG}"
    "github_repo:${DD_HOOK_TELEMETRY_GITHUB_REPO}"
    "username:$(dd_hook_telemetry_username)"
    "hook_chain_fix:${DD_HOOK_CHAIN_FIX_TAG}"
  )
  local tag
  for tag in "$@"; do
    [ -n "$tag" ] && tags+=("$tag")
  done

  local tag_string
  tag_string="$(IFS=,; printf '%s' "${tags[*]}")"
  local payload="${metric}:1|c|#${tag_string}"
  _dd_hook_telemetry_send "$payload"
}

dd_hook_telemetry_autohook_execution() {
  local hook_type="$1"
  local blocked="${2:-false}"
  local scripts_count="${3:-0}"

  dd_hook_telemetry_count \
    "dd.trace.repo_hooks.autohook.executions" \
    "hook:$(_dd_hook_telemetry_sanitize "$hook_type")" \
    "blocked:$(_dd_hook_telemetry_sanitize "$blocked")" \
    "scripts_count:$(_dd_hook_telemetry_sanitize "$scripts_count")"
}

dd_hook_telemetry_hook_chain_bypass() {
  local bypass_kind="${1:-git_hooks_override}"
  dd_hook_telemetry_count \
    "dd.trace.repo_hooks.hook_chain.bypass" \
    "state:active" \
    "bypass_kind:$(_dd_hook_telemetry_sanitize "$bypass_kind")"
}

dd_hook_telemetry_hook_chain_repair() {
  dd_hook_telemetry_count \
    "dd.trace.repo_hooks.hook_chain.repair" \
    "action:unset_hooks_path" \
    "outcome:repaired"
}
