#!/bin/bash
./test_build.sh -C || { echo "Failed cppcheck"; exit 1; }
./test_build.sh -s || { echo "Failed safety tests"; exit 1; }
./test_build.sh -f || { echo "Failed -fanalyzer"; exit 1; }
./test_build.sh -t || { echo "Failed threading sanitizer"; exit 1; }
./test_build.sh -n || { echo "Failed numeric sanitizer"; exit 1; }
./test_build.sh -d || { echo "Failed dataflow sanitizer"; exit 1; }
#./test_build.sh -m || { echo "Failed memory leak sanitizer"; exit 1; }
