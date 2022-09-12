#!/usr/bin/env bash
set -ex

if [ -z "$1" ]
then
      echo "\No arguments. Stop execution"
      exit
else
      echo "Scenario: $1";
      echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope
fi

mkdir -p results

export PATH=$PATH:../
export PYTHONPATH=$PYTHONPATH:../

py-spy record --native --format speedscope --rate 300 -o results/prof_$1.prof -- python scripts/prof_$1.py