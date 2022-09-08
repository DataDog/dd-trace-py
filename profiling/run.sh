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

export PATH=$PATH:../
export PYTHONPATH=$PYTHONPATH:../
export ENABLED=true
py-spy record --native --format speedscope --rate 300 -o results/prof_$1_enabled.prof -- python appsec/prof_$1.py


export ENABLED=""
py-spy record --native --format speedscope --rate 300 -o results/prof_$1_disabled.prof -- python appsec/prof_$1.py