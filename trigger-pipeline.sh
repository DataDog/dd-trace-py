#!/usr/bin/env bash
set -euo pipefail

REF="$1"
if [ -z "$REF" ]; then
    echo "Usage: $0 <git-ref>"
    exit 1
fi

# Get auth token with ddtool if available, otherwise install and use authanywhere
if command -v ddtool 2>&1 > /dev/null; then
    AUTH_TOKEN=$(ddtool auth token rapid-devex-ci --datacenter us1.ddbuild.io)
else
    if ! command -v authanywhere 2>&1 > /dev/null; then
        if command -v brew 2>&1 > /dev/null; then
            brew install authanywhere
        else
            if [ $(uname -m) = x86_64 ]; then
                AAA="amd64"
            else
                AAA="arm64"
            fi
            curl -s -OL "binaries.ddbuild.io/dd-source/authanywhere/LATEST/authanywhere-linux-${AAA}"
            chmod +x "authanywhere-linux-${AAA}"
            mkdir -p /tmp/bin/
            mv "authanywhere-linux-${AAA}" /tmp/bin/authanywhere
            export PATH="/tmp/bin/:$PATH"
        fi
    fi

    AUTH_TOKEN=$(authanywhere --audience rapid-devex-ci --dc us1.ddbuild.io --raw true)
fi


if ! command -v jq 2>&1 > /dev/null; then
    if command -v brew 2>&1 > /dev/null; then
        brew install jq
    else
        if [ $(uname -m) = x86_64 ]; then
            AAA="amd64"
        else
            AAA="arm64"
        fi

        curl -s -OL "https://github.com/jqlang/jq/releases/download/jq-1.8.1/jq-linux-${AAA}"
        chmod +x "jq-linux-${AAA}"
        mkdir -p /tmp/bin
        mv "jq-linux-${AAA}" /tmp/bin/jq
        export PATH="/tmp/bin/:$PATH"
    fi
fi

API_URL="https://bti-ci-api.us1.ddbuild.io/internal/ci/gitlab/pipeline/DataDog/dd-trace-py"

PAYLOAD=$(cat <<EOF
{
    "ref": "$REF",
    "variables": {
        "NIGHTLY_BENCHMARKS": "$([ -z "$UNPIN_DEPENDENCIES" ] && echo "true" || echo "false")",
        "NIGHTLY_BUILD": "true"
        "UNPIN_DEPENDENCIES": "${UNPIN_DEPENDENCIES:-false}"
    }
}
EOF
)

echo "Triggering pipeline for ref: $REF"
RESPONSE=$(
    curl -s -X POST "$API_URL" \
    -H "Authorization: Bearer $AUTH_TOKEN" \
    -H "Content-Type: application/vnd.api+json" \
    -d "$PAYLOAD"
    -vv
)

echo "Response from API:"
echo $(echo $RESPONSE | jq '.')

PIPELINE_ID=$(echo "$RESPONSE" | jq -r '.pipeline.id')
PIPELINE_SHA=$(echo "$RESPONSE" | jq -r '.pipeline.sha')
PIPELINE_URL=$(echo "$RESPONSE" | jq -r '.pipeline.webUrl')
echo ""
echo "==============================="
echo "Triggered pipeline ID: $PIPELINE_ID"
echo "Pipeline SHA: $PIPELINE_SHA"
echo "Pipeline URL: $PIPELINE_URL"
