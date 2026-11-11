#!/usr/bin/env bash
# Enforce how Cargo.toml files depend on libdatadog.
#
# libdatadog crates are versioned and released together, so every git
# dependency on it must:
#   1. pin a release tag (`rev = "vX.Y.Z"`), not a commit SHA or a branch, and
#   2. use the same release tag as every other libdatadog dependency.
# Pinning a development commit or mixing releases ships unreleased native code
# and pulls duplicate copies of libdatadog code into the native extension.
#
# A dependency that deliberately needs a non-release pin for a short time (for
# example while a libdatadog change is awaiting its release) can opt out by
# ending its line with the marker `# libdatadog-pin: allow`.
#
# Usage: bash scripts/check-libdatadog-pins.sh   (run from anywhere inside the repo)
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

status=0
tags=()

# Matches `git = "https://github.com/DataDog/libdatadog"` (optionally `.git`,
# or an ssh-style URL) on a non-comment line.
dep_re='^[^#]*git[[:space:]]*=[[:space:]]*"[^"]*github\.com[/:]DataDog/libdatadog(\.git)?/?"'
tag_re='rev[[:space:]]*=[[:space:]]*"(v[0-9]+\.[0-9]+\.[0-9]+)"'

while IFS= read -r file; do
  while IFS= read -r hit; do
    lineno=${hit%%:*}
    line=${hit#*:}
    case "$line" in
      *"libdatadog-pin: allow"*) continue ;;
    esac
    if [[ "$line" =~ $tag_re ]]; then
      tags+=("${BASH_REMATCH[1]}")
    else
      echo "::error file=${file},line=${lineno}::libdatadog dependency must pin a release tag on the same line, e.g. rev = \"vX.Y.Z\" (not a commit SHA, branch or tag key)"
      status=1
    fi
  done < <(grep -nEi "$dep_re" "$file" || true)
done < <(git ls-files | grep -E '(^|/)Cargo\.toml$' || true)

if [ "${#tags[@]}" -gt 0 ]; then
  distinct=$(printf '%s\n' "${tags[@]}" | sort -uV | paste -sd' ' -)
  if [ "$(wc -w <<<"$distinct")" -gt 1 ]; then
    echo "::error::libdatadog dependencies must all pin the same release, found: ${distinct}"
    status=1
  fi
  [ "$status" -eq 0 ] && echo "OK: ${#tags[@]} libdatadog dependencies pinned to ${distinct}"
fi
exit "$status"
