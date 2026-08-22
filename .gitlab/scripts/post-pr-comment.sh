#!/usr/bin/env bash
# Post a comment to a PR via the internal pr-commenter service.
# No-ops if the message file is empty.
#
# Usage: post-pr-comment.sh <header> <message-file>
#
#   header        Title shown above the comment body.
#   message-file  Path to a file whose contents become the comment body.

set -euo pipefail

HEADER="$1"
MESSAGE_FILE="$2"

# Bail out silently if there is nothing to say.
[ -s "$MESSAGE_FILE" ] || exit 0

AUTHANYWHERE_DIR="$(mktemp -d)"
trap 'rm -rf "$AUTHANYWHERE_DIR"' EXIT
PAYLOAD_FILE="$AUTHANYWHERE_DIR/payload.json"
COMMENT_FILE="$AUTHANYWHERE_DIR/comment.txt"

awk 'BEGIN { limit = 60000 } { size += length($0) + 1; if (size > limit) exit; print }' "$MESSAGE_FILE" > "$COMMENT_FILE"
if ! cmp -s "$MESSAGE_FILE" "$COMMENT_FILE"; then
  printf '\nOutput truncated; see the CI job log for the complete report.\n' >> "$COMMENT_FILE"
fi

jq --null-input \
  --arg commit "$CI_COMMIT_SHORT_SHA" \
  --rawfile message "$COMMENT_FILE" \
  --arg header "$HEADER" \
  '{commit: $commit, message: $message, header: $header, org: "Datadog", repo: "dd-trace-py"}' \
  > "$PAYLOAD_FILE"

wget -nv -P "$AUTHANYWHERE_DIR" binaries.ddbuild.io/dd-source/authanywhere/LATEST/authanywhere-linux-amd64
chmod +x "$AUTHANYWHERE_DIR/authanywhere-linux-amd64"

curl 'https://pr-commenter.us1.ddbuild.io/internal/cit/pr-comment' \
  -H "$("$AUTHANYWHERE_DIR/authanywhere-linux-amd64")" \
  -H 'Content-Type: application/json' \
  -X PATCH \
  --data-binary "@$PAYLOAD_FILE"
