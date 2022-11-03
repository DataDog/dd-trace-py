#!/usr/bin/env python3
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


def generate_main_workflow():
    defined_jobs = get_jobs_from_riot(venv)
    BASE_JOBS = {"pre_check", "ccheck", "build_base_venvs"}
    NO_COVERAGE = BASE_JOBS | {"coverage_report", "graphene", "build_docs", "internal"}
    circleci_config["jobs"].update(defined_jobs)
    list_coverage = [job for job in circleci_config["jobs"] if job not in NO_COVERAGE]
    DEFAULT_REQUIREMENTS = ["pre_check", "ccheck", "build_base_venvs"]
    CHECK_REQUIREMENTS = ["pre_check", "ccheck"]
    CHECKONLY_JOBS = ["build_docs"] + [f"profile-windows-3{i}" for i in (5, 6, 8, 9, 10)]

    # Define the requirements for each tests. Currently most tests are using the same
    # requirements and coverage reports are after all other tests.
    requirements = collections.defaultdict(lambda: DEFAULT_REQUIREMENTS)
    for jobs, reqs in [(BASE_JOBS, []), (CHECKONLY_JOBS, CHECK_REQUIREMENTS), (["coverage_report"], list_coverage)]:
        for job in jobs:
            requirements[job] = reqs

    # Populating the jobs of tests with the appropriate requirements
    circleci_config["workflows"]["test"]["jobs"] = []
    for name in circleci_config["jobs"]:
        circleci_config["workflows"]["test"]["jobs"].append({name: {"requires": requirements[name]}})

    # nightly tests are the same as tests
    circleci_config["workflows"]["test_nightly"]["jobs"] = circleci_config["workflows"]["test"]["jobs"]

    yaml.dump(circleci_config, sys.stdout, default_flow_style=False)


if __name__ == "__main__":
    generate_main_workflow()
