#!/usr/bin/env bash
# Open GitHub compare URLs to create draft PRs in stack order (1→14).
# Skips entries that already have a PR number in pr-numbers.env.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "$DIR/pr-numbers.env"

"$DIR/generate-pr-urls.sh" >/dev/null

opened=0
for i in $(seq 1 14); do
  var="PR${i}"
  num="${!var:-}"
  if [[ -n "$num" ]]; then
    echo "SKIP ${i}/14 — PR #${num} already exists"
    continue
  fi
  url_file="${DIR}/pr-bodies/$(printf '%02d' "$i")-compare.url"
  if [[ ! -f "$url_file" ]]; then
    echo "WARN: missing ${url_file}" >&2
    continue
  fi
  url="$(cat "$url_file")"
  echo "OPEN ${i}/14 — ${url}"
  if command -v open >/dev/null 2>&1; then
    open "$url"
    opened=$((opened + 1))
    # Avoid browser tab flood; pause between opens.
    sleep 2
  else
    echo "$url"
  fi
done

if [[ $opened -eq 0 ]] && command -v open >/dev/null 2>&1; then
  echo "No compare URLs opened (all PR slots filled or open(1) unavailable)."
  echo "Compare links: ${DIR}/DRAFT_PRS.md"
fi
