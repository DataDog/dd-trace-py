#!/usr/bin/env bash
set -ex -o pipefail

ARTIFACTS_DIR="/artifacts/${CI_JOB_ID}"
mkdir -p "${ARTIFACTS_DIR}"
mkdir -p "${ARTIFACTS_DIR}/baseline"
mkdir -p "${ARTIFACTS_DIR}/candidate"

# Setup benchmark scenario code
mkdir -p "/app/benchmarks"
cp -a "${CI_PROJECT_DIR:-.}/benchmarks/base/." "/app/benchmarks/"
cp -a "${CI_PROJECT_DIR:-.}/benchmarks/bm" "/app/benchmarks/"
cp -a "${CI_PROJECT_DIR:-.}/benchmarks/$SCENARIO/." "/app/benchmarks/"
cd /app/benchmarks

# Run candidate benchmarks
CANDIDATE_WHL="${CI_PROJECT_DIR}/${CANDIDATE_WHL}"
if [ ! -f "$CANDIDATE_WHL" ]; then
  echo "Candidate wheel not found: $CANDIDATE_WHL"
  exit 1
fi
python3 -m venv .venv_candidate
source .venv_candidate/bin/activate
pip install "${CANDIDATE_WHL}" -r requirements.txt
python run.py "$ARTIFACTS_DIR/candidate"
deactivate

# Run baseline benchmarks
BASELINE_WHL="${CI_PROJECT_DIR}/${BASELINE_WHL}"
if [ -f "$BASELINE_WHL" ]; then
  python3 -m venv .venv_baseline
  source .venv_baseline/bin/activate
  pip install "${BASELINE_WHL}" -r requirements.txt
  python run.py "$ARTIFACTS_DIR/baseline"
  deactivate
else
  echo "Baseline wheel not found: $BASELINE_WHL"
fi
