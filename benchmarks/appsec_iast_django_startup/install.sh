#!/usr/bin/env bash
set -euo pipefail

apt-get update && apt-get install --no-install-recommends -y \
  sqlite3 ffmpeg libsm6 libxext6 \
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*
