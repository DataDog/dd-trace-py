#!/bin/sh

# This script is used by the admission controller to install the library from the
# init container into the application container.
cp sitecustomize.py "$1/sitecustomize.py"
cp -r ddtrace_pkgs "$1/ddtrace_pkgs"

# Create a custom site-packages directory to install the package into.
mkdir "$1/site-packages"

# Need a tmp directory for pip to use when installing the wheels to the custom site-packages directory
# above.
mkdir -p "$1/tmp"
