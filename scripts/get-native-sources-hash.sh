#!/usr/bin/env bash
set -e -o pipefail

# Dump the installed versions of Python
pyenv versions | sort --numeric-sort > pyenv_versions

# Generate a hash of the hash of all the files needed to build native extensions
find ddtrace src setup.py pyproject.toml pyenv_versions \
     -name '*.c' -or \
     -name '*.cpp' -or \
     -name '*.pyx' -or \
     -name '*.h' -or \
     -name 'CMakeLists.txt' -or \
     -name 'Cargo.lock' -or \
     -name '*.rs' -or \
     -name 'setup.py' -or \
     -name 'pyproject.toml' -or \
     -name 'pyenv_versions' | \
    sort | \
    xargs sha256sum | \
    sha256sum | \
    awk '{print $1}'
