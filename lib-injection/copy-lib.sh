#!/bin/sh

# This script is used by the admission controller to install the library from the
# init container into the application container.
cp sitecustomize.py "$1/sitecustomize.py"
if [ -e "dd-trace-py" ]; then
    cp -R dd-trace-py "$1/"
fi
