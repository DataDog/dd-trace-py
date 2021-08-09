#!/usr/bin/env python3

import os
import subprocess
import sys

import yaml


def read_config(path):
    with open(path, "r") as fp:
        return yaml.load(fp, Loader=yaml.FullLoader)


def run(scenario_py, cname, cvars, output_dir):
    cmd = [
        "python",
        scenario_py,
        # necessary to copy PYTHONPATH for venvs
        "--copy-env",
        "--append",
        os.path.join(output_dir, "results.json"),
        "--name",
        cname,
    ]
    for (cvarname, cvarval) in cvars.items():
        cmd.append("--{}".format(cvarname))
        cmd.append(str(cvarval))
    proc = subprocess.Popen(cmd)
    proc.wait()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: {} <output dir>".format(sys.argv[0]))
        sys.exit(1)

    output_dir = sys.argv[1]
    print("Saving results to {}".format(output_dir))
    config = read_config("config.yaml")
    for (cname, cvars) in config.items():
        run("scenario.py", cname, cvars, output_dir)
