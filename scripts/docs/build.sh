#!/usr/bin/env bash

set -eux

reno lint
sphinx-build -b spelling docs docs/_build/html
sphinx-build -b html docs docs/_build/html
