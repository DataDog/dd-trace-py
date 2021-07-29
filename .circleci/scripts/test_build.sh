#!/usr/bin/env bash
set -eux -o pipefail

if [[ "$OSTYPE" == "msys" ]]; then
    # Install python version and create a virtuallenv
    nuget install python -Version $PYTHON_VERSION -ExcludeVersion -OutputDirectory .
    ./python/tools/python.exe --version
    ./python/tools/python.exe -m pip install virtualenv
    ./python/tools/python.exe -m virtualenv env
    # When running script under Windows executor we need to activate the venv
    # created for the specific Python version
    source env/Scripts/activate
fi

# Install required dependencies
# DEV: `wheel` is needed to run `bdist_wheel`
pip install twine readme_renderer[md] wheel cython
# Ensure we didn't cache from previous runs
rm -rf build/ dist/
# Manually build any extensions to ensure they succeed
python setup.py build_ext --force
# Ensure source package will build
python setup.py sdist
# Ensure wheel will build
python setup.py bdist_wheel
# Ensure package long description is valid and will render
# https://github.com/pypa/twine/tree/6c4d5ecf2596c72b89b969ccc37b82c160645df8#twine-check
twine check dist/*
