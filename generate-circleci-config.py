#!/usr/bin/env python3
from argparse import ArgumentParser
import collections
import sys

import yaml

from circle_config_init import circleci_config
from riotfile import Venv
from riotfile import venv


def get_jobs_from_riot(venv: Venv) -> dict:
    """
    read the riot python file and retrieve configuration information
    to define properly the circle ci tests
    """
    result_dict = {}
    stack = [venv]
    while stack:
        v = stack.pop()
        if v.ci:
            result_dict[v.name] = v.ci
        stack.extend(v.venvs)
    return result_dict


def generate_main_workflow(latest: bool) -> None:
    defined_jobs = get_jobs_from_riot(venv)
    circleci_config["jobs"].update(defined_jobs)

    BASE_JOBS = {"pre_check", "ccheck", "build_base_venvs"}
    NO_COVERAGE = BASE_JOBS | {"coverage_report", "graphene", "build_docs", "internal"}
    CHECKONLY_JOBS = ["build_docs"] + [f"profile-windows-3{i}" for i in (5, 6, 8, 9, 10)]
    DEFAULT_REQUIREMENTS = ["pre_check", "ccheck", "build_base_venvs"]
    CHECK_REQUIREMENTS = ["pre_check", "ccheck"]
    COVERAGE_REQUIREMENTS = [job for job in circleci_config["jobs"] if job not in NO_COVERAGE]

    test_workflow = "latest_test" if latest else "test"

    # fast tests to check everything's ok quickly
    KEEP_TESTS = NO_COVERAGE | {"aredis", "pylons", "jinja2", "debugger"}

    for j in list(circleci_config["jobs"]):
        if j not in KEEP_TESTS:
            del circleci_config["jobs"][j]
    COVERAGE_REQUIREMENTS = [job for job in circleci_config["jobs"] if job not in NO_COVERAGE]
    # end fast tests

    # Define the requirements for each tests. Currently most tests are using the same
    # requirements and coverage reports are after all other tests.
    requirements = collections.defaultdict(lambda: DEFAULT_REQUIREMENTS)
    for jobs, reqs in [
        (BASE_JOBS, []),
        (CHECKONLY_JOBS, CHECK_REQUIREMENTS),
        (["coverage_report"], COVERAGE_REQUIREMENTS),
    ]:
        for job in jobs:
            requirements[job] = reqs

    if not latest:
        # build the latest continuance
        circleci_config["jobs"]["setup_latest"] = {
            "executor": "continuation/default",
            "steps": [
                "checkout",
                {
                    "run": {
                        "name": "Generate config for latest workflow",
                        "command": "./generate-circleci-config.py --latest > generated_config_latest.yml",
                        "environment": {"DD_USE_LATEST_VERSIONS": "true"},
                    }
                },
                {"continuation/continue": {"configuration_path": "generated_config_latest.yml"}},
            ],
        }
        requirements["setup_latest"] = ["coverage_report"]

    # Populating the jobs of tests with the appropriate requirements
    circleci_config["workflows"][test_workflow] = {"jobs": []}
    for name in circleci_config["jobs"]:
        circleci_config["workflows"][test_workflow]["jobs"].append({name: {"requires": requirements[name]}})

    if latest:
        # patch tests to use latest version of packages
        run_test = circleci_config["commands"]["run_test"]["steps"][3]["when"]["steps"][2]["run"]
        run_test["environment"]["DD_USE_LATEST_VERSIONS"] = "true"
    else:
        # nightly tests are the same as tests but with specific triggers
        circleci_config["workflows"]["test_nightly"] = {
            "triggers": [{"schedule": {"cron": "0 0 * * *", "filters": {"branches": {"only": ["0.x", "1.x"]}}}}],
        }
        circleci_config["workflows"]["test_nightly"]["jobs"] = circleci_config["workflows"]["test"]["jobs"]

    yaml.dump(circleci_config, sys.stdout, default_flow_style=False)


if __name__ == "__main__":
    parser = ArgumentParser(
        prog="generate-circleci-config", description="Automatically generate CircleCI configuration yaml file"
    )
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()
    generate_main_workflow(args.latest)
