#!/usr/bin/env python3
import collections
from copy import deepcopy
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


def generate_main_workflow() -> None:
    defined_jobs = get_jobs_from_riot(venv)
    circleci_config["jobs"].update(defined_jobs)

    BASE_JOBS = {"pre_check", "ccheck", "build_base_venvs"}
    NO_COVERAGE = BASE_JOBS | {"coverage_report", "graphene", "build_docs", "internal"}
    CHECKONLY_JOBS = ["build_docs"] + [f"profile-windows-3{i}" for i in (5, 6, 8, 9, 10)]
    BASE_REQUIREMENTS = []
    DEFAULT_REQUIREMENTS = ["pre_check", "ccheck", "build_base_venvs"]
    CHECK_REQUIREMENTS = ["pre_check", "ccheck"]
    COVERAGE_REQUIREMENTS = [job for job in circleci_config["jobs"] if job not in NO_COVERAGE]

    test_workflow = "test"

    # fast tests to check everything's ok quickly
    KEEP_TESTS = NO_COVERAGE | {"aredis", "pylons", "jinja2"}

    for j in list(circleci_config["jobs"]):
        if j not in KEEP_TESTS:
            del circleci_config["jobs"][j]
    COVERAGE_REQUIREMENTS = [job for job in circleci_config["jobs"] if job not in NO_COVERAGE]
    # end fast tests

    # Define the requirements for each tests. Currently most tests are using the same
    # requirements and coverage reports are after all other tests.
    requirements = collections.defaultdict(lambda: DEFAULT_REQUIREMENTS)
    for jobs, reqs in [
        (BASE_JOBS, BASE_REQUIREMENTS),
        (CHECKONLY_JOBS, CHECK_REQUIREMENTS),
        (["coverage_report"], COVERAGE_REQUIREMENTS),
    ]:
        for job in jobs:
            requirements[job] = reqs

    # Populating the jobs of tests with the appropriate requirements and environment
    circleci_config["workflows"][test_workflow] = {"jobs": []}
    for name in circleci_config["jobs"]:
        circleci_config["workflows"][test_workflow]["jobs"].append({name: {"requires": requirements[name]}})
        if "environment" not in circleci_config["jobs"][name]:
            circleci_config["jobs"][name]["environment"] = []
        circleci_config["jobs"][name]["environment"].append({"DD_USE_LATEST_VERSIONS": "false"})
        # if latest and name == "pre_check":
        #     circleci_config["workflows"][test_workflow]["jobs"][-1][name]["type"] = "approval"
    # if latest:
    #     # patch tests to use latest version of packages
    #     run_test = circleci_config["commands"]["run_test"]["steps"][3]["when"]["steps"][2]["run"]
    #     run_test["environment"]["DD_USE_LATEST_VERSIONS"] = "true"
    # else:

    # nightly tests are the same as tests but with specific triggers
    circleci_config["workflows"]["test_nightly"] = {
        "triggers": [{"schedule": {"cron": "0 0 * * *", "filters": {"branches": {"only": ["0.x", "1.x"]}}}}],
    }
    circleci_config["workflows"]["test_nightly"]["jobs"] = circleci_config["workflows"]["test"]["jobs"]

    def latest_name(name):
        return name + "_latest"

    def latest_requirements(name, cache_req=[], cache_req_latest=[]):
        initial_req = requirements[name]
        for req, req_l in zip(cache_req, cache_req_latest):
            if initial_req is req:
                return req_l
        cache_req.append(initial_req)
        res = [latest_name(n) for n in initial_req]
        if not res:
            res.append("wait_for_approval")
        cache_req_latest.append(res)
        return res

    # Build latest_workflow
    test_latest = latest_name(test_workflow)
    circleci_config["workflows"][test_latest] = {"jobs": []}
    #
    for name in list(circleci_config["jobs"]):
        lname = latest_name(name)
        circleci_config["workflows"][test_latest]["jobs"].append({lname: {"requires": latest_requirements(name)}})
        circleci_config["jobs"][lname] = deepcopy(circleci_config["jobs"][name])
        circleci_config["jobs"][lname]["environment"][-1]["DD_USE_LATEST_VERSIONS"] = "true"
    circleci_config["jobs"]["wait_for_approval"] = {
        "executor": "python310",
        "steps": [
            {"run": {"name": "Waiting for your approval", "command": "echo Waiting for your approval"}},
        ],
    }
    circleci_config["workflows"][test_latest]["jobs"].append({"wait_for_approval": {"type": "approval"}})
    yaml.dump(circleci_config, sys.stdout, default_flow_style=False)


if __name__ == "__main__":
    generate_main_workflow()
