#!/usr/bin/env python3

import json
import os
import subprocess
import sys

import yaml


SHOULD_PROFILE = os.environ.get("PROFILE_BENCHMARKS", "0") == "1"


def read_config(path):
    with open(path, "r") as fp:
        return yaml.load(fp, Loader=yaml.FullLoader)


def run(scenario_py, cname, cvars, output_dir):
    if SHOULD_PROFILE:
        # viztracer won't create the missing directory itself
        viztracer_output_dir = os.path.join(output_dir, "viztracer")
        os.makedirs(viztracer_output_dir, exist_ok=True)

        cmd = [
            "viztracer",
            "--minimize_memory",
            "--min_duration",
            "5",
            "--max_stack_depth",
            "200",
            "--output_file",
            os.path.join(output_dir, "viztracer", "{}.json".format(cname)),
        ]
    else:
        cmd = ["python"]

    cmd += [
        scenario_py,
        # necessary to copy PYTHONPATH for venvs
        "--copy-env",
        "--append",
        os.path.join(output_dir, "results.json"),
        "--name",
        cname,
    ]
    for cvarname, cvarval in cvars.items():
        cmd.append("--{}".format(cvarname))
        if isinstance(cvarval, (dict, list)):
            # convert dicts and lists to JSON strings
            cmd.append(json.dumps(cvarval))
        else:
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

    # Filter configs if BENCHMARK_CONFIGS is set
    benchmark_configs = os.environ.get("BENCHMARK_CONFIGS")
    if benchmark_configs:
        allowed_configs = set(c.strip() for c in benchmark_configs.split(","))
        config = {k: v for k, v in config.items() if k in allowed_configs}
        print("Filtering to configs: {}".format(", ".join(sorted(config.keys()))))

    for cname, cvars in config.items():
        run("scenario.py", cname, cvars, output_dir)
