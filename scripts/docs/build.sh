#!/usr/bin/env bash

set -eux

reno lint
sphinx-build -W -b spelling docs docs/_build/html
sphinx-build -W -b html docs docs/_build/html
