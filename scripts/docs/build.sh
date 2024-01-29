#!/usr/bin/env bash
set -eux

reno lint
sphinx-build -vvv -W -b spelling docs docs/_build/html
sphinx-build -vvv -W -b html docs docs/_build/html
