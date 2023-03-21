#!/bin/sh

# This script is used by the admission controller to install the library from the
# init container into the application container.
cp sitecustomize.py "$1/sitecustomize.py"
