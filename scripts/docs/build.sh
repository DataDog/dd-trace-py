#!/usr/bin/env bash

export LOCAL_DOCS_BUILD=1
export DD_TRACE_ENABLED=0

set -eux

reno lint
sphinx-build -vvv -W -b spelling docs docs/_build/html
sphinx-build -vvv -W -b html docs docs/_build/html
