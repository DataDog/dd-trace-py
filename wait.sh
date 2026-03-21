set -euo pipefail

TARGET="${1:?Usage: $0 <dd-source | dogweb>}"

INTEGRATION_BRANCH="profiling-integration"

CURRENT_BRANCH="$(git branch --show-current 2>/dev/null || true)"
if [ -n "$CURRENT_BRANCH" ] && [ "$CURRENT_BRANCH" != "$INTEGRATION_BRANCH" ]; then
    echo "Merging '$CURRENT_BRANCH' into '$INTEGRATION_BRANCH'..."
    git fetch origin "$INTEGRATION_BRANCH" > /dev/null 2>&1
    git checkout -B "$INTEGRATION_BRANCH" origin/"$INTEGRATION_BRANCH"
    git merge "$CURRENT_BRANCH" --no-edit
    git push -u origin "$INTEGRATION_BRANCH"
fi

export JOB_NAME="upload manylinux2014"

PIPELINE_ID=$(python3 wait_ci.py --branch "$INTEGRATION_BRANCH" "$JOB_NAME")
echo "Pipeline ID: $PIPELINE_ID"

if [ "$TARGET" = "dd-source" ]; then
    (cd ../dd-source && ../ddtpy/update_ddtrace_wheels.sh "$PIPELINE_ID")
elif [ "$TARGET" = "dogweb" ]; then
    (cd ../dogweb && ../ddtpy/upgrade_ddtrace_dockerfile.sh "$PIPELINE_ID")
else
    echo "Invalid target: $TARGET"
    exit 1
fi
