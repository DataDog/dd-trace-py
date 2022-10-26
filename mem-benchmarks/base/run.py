#!/usr/bin/env python3


import os
import subprocess
import sys

from statistics import median
import yaml
from memray import FileDestination, FileReader, Tracker

FILENAME = "memray.bin"


def read_config(path):
    with open(path, "r") as fp:
        return yaml.load(fp, Loader=yaml.FullLoader)


def run(scenario_py, cname, cvars, output_dir):
    _file = FileDestination(FILENAME, overwrite=True)
    with Tracker(
        destination=_file, native_traces=True, follow_fork=True,
        memory_interval_ms=1
    ):


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

    with FileReader(FILENAME) as _file:
        print(
            "Max Memory: ",
            _file.metadata.peak_memory,
            "\nAllocations: ",
            _file.metadata.total_allocations,
        )
        for feat in ["heap", "rss"]:
            L = [getattr(ms, feat) for ms in _file.get_memory_snapshots()]
            if L:
                print(feat, "Median :", median(L))

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: {} <output dir>".format(sys.argv[0]))
        sys.exit(1)

    output_dir = sys.argv[1]
    print("Saving results to {}".format(output_dir))
    config = read_config("config.yaml")
    for (cname, cvars) in config.items():
        run("scenario.py", cname, cvars, output_dir)
