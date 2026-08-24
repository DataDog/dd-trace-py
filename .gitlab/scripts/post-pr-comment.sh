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

MESSAGE="$(awk '{printf "%s\\n", $0}' "$MESSAGE_FILE" | sed 's/\"/\\"/g')"

AUTHANYWHERE_DIR="$(mktemp -d)"
trap 'rm -rf "$AUTHANYWHERE_DIR"' EXIT
wget -nv -P "$AUTHANYWHERE_DIR" binaries.ddbuild.io/dd-source/authanywhere/LATEST/authanywhere-linux-amd64
chmod +x "$AUTHANYWHERE_DIR/authanywhere-linux-amd64"

curl 'https://pr-commenter.us1.ddbuild.io/internal/cit/pr-comment' \
  -H "$("$AUTHANYWHERE_DIR/authanywhere-linux-amd64")" \
  -X PATCH -d "{ \
    \"commit\": \"$CI_COMMIT_SHORT_SHA\", \
    \"message\": \"$MESSAGE\", \
    \"header\": \"$HEADER\", \
    \"org\": \"Datadog\", \
    \"repo\": \"dd-trace-py\" \
  }"
