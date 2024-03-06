#!/bin/bash
set -euox pipefail

./setup_custom.sh -C || { echo "Failed cppcheck"; exit 1; }
./setup_custom.sh -s || { echo "Failed safety tests"; exit 1; }
./setup_custom.sh -f || { echo "Failed -fanalyzer"; exit 1; }
./setup_custom.sh -t || { echo "Failed threading sanitizer"; exit 1; }
./setup_custom.sh -n || { echo "Failed numeric sanitizer"; exit 1; }
./setup_custom.sh -d || { echo "Failed dataflow sanitizer"; exit 1; }
#./setup_custom.sh -m || { echo "Failed memory leak sanitizer"; exit 1; } # Need to propagate msan configuration, currently failing in googletest internals
