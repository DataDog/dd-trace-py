#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR)
riot -v run -s hook-codespell $staged_files
