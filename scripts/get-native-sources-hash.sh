#!/usr/bin/env bash
set -e -o pipefail

# This script now uses the new component-based hashing system
# while maintaining backward compatibility with DD_NATIVE_SOURCES_HASH

# Check if the new component hash script is available
if command -v python3 >/dev/null 2>&1 && python3 scripts/get_component_hashes.py --component combined >/dev/null 2>&1; then
    # Use the new component-based system
    echo "INFO: Using component-based hashing system" >&2
    python3 scripts/get_component_hashes.py --component combined
else
    # Fall back to legacy behavior
    echo "INFO: Falling back to legacy hashing system" >&2
    
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
fi
