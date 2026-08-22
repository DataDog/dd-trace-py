#!/usr/bin/env bash
set -e -u -o pipefail

SUITE_NAME="${1:-}"
scripts/test-env list "${SUITE_NAME}" | ./.gitlab/ci-split-input.sh
