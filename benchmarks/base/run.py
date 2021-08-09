#!/usr/bin/env python3

import os
import subprocess
import sys

import yaml


def read_config(path):
    with open(path, "r") as fp:
        return yaml.load(fp)


def run(name, spec, output):
    cmd = ["python", "scenario.py", "--copy-env", "--name", name, "--append", output]
    for (arg, val) in spec.items():
        cmd.append("--{}".format(arg))
        cmd.append(str(val))
    proc = subprocess.Popen(cmd)
    proc.wait()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(1)

    scenario = sys.argv[1]
    output = os.path.join(sys.argv[2], "results.json")
    print("Saving results to {}".format(output))
    config = read_config(sys.argv[1])
    for (name, spec) in config.items():
        run(name, spec, output)
