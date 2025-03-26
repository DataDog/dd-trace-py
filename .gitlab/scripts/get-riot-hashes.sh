#!/usr/bin/env bash
set -e -u -o pipefail

SUITE_NAME="${1:-}"
riot list --hash-only "${SUITE_NAME}" | sort | ./.gitlab/ci-split-input.sh
