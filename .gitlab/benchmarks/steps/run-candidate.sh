#!/usr/bin/env bash
set -ex -o pipefail

# Use a dedicated pip cache subdirectory so that baseline and candidate pip
# installs — which run concurrently — don't corrupt each other's cache.
# Both subdirectories live under the GitLab-cached .cache/pip, so the existing
# cache config (paths: [.cache/pip]) still captures them without changes.
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-.cache/pip}/candidate"

python3 -m venv .venv_candidate
source .venv_candidate/bin/activate
pip install "${CANDIDATE_WHL}" -r requirements.txt
python run.py "$ARTIFACTS_DIR/candidate"
