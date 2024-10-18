# This script is used to generate the CircleCI dynamic config file in
# .circleci/config.gen.yml.
#
# To add new configuration manipulations that are based on top of the template
# file in .circleci/config.templ.yml, add a function named gen_<name> to this
# file. The function will be called automatically when this script is run.

SUITES = {
    "debugger",
}


def print_job(name, base, services, variables, parallel, file=None):
    print(f"{name}:", file=file)

    print(f"  extends: {base}", file=file)

    if services:
        print("  services:", file=file)
        for service in services:
            print(f"    - {service}", file=file)

    if parallel:
        print(f"  parallel: {parallel}", file=file)

    print("  variables:", file=file)
    for key, value in variables.items():
        print(f"    {key}: {value}", file=file)

    print(file=file)


def convert_circleci_config():
    gitlab_jobs = {}
    jobs = {}

    SERVICE_VARIABLES = {
        "postgres": {"TEST_POSTGRES_HOST": "postgres"},
        "redis": {"TEST_REDIS_HOST": "redis"},
        "memcached": {"TEST_MEMCACHED_HOST": "memcached"},
    }

    CONFIG_TEMPLATE_FILE = ROOT / ".circleci" / "config.templ.yml"
    with YAML(output=TESTS / "jobs.yml") as yaml, (GITLAB / "alltests.yml").open("w") as out:
        LOGGER.info("Loading configuration template from %s", CONFIG_TEMPLATE_FILE)
        config = yaml.load(CONFIG_TEMPLATE_FILE)

        for name, job in config["jobs"].items():
            try:
                run_test = job["steps"][0]["run_test"]
                runner = "riot"
            except Exception:
                try:
                    run_test = job["steps"][0]["run_hatch_env_test"]
                    runner = "hatch"
                except Exception:
                    continue

            is_snapshot = run_test.get("snapshot", False)
            docker_services = set(run_test.get("docker_services", "").split())
            services = [f"!reference [.services, {_}]" for _ in docker_services]
            if is_snapshot:
                services.insert(0, "!reference [.test_base_riot_snapshot, services]")

            variables = {"SUITE_NAME": name if runner == "riot" else run_test["env"]}
            for service in docker_services:
                variables.update(SERVICE_VARIABLES.get(service, {}))

            jobs[name] = {
                "is_snapshot": is_snapshot,
            }

            base = (
                (".test_base_riot_snapshot" if is_snapshot else ".test_base_riot")
                if runner == "riot"
                else ".test_base_hatch"
            )

            print_job(name, base, services, variables, job.get("parallelism"), file=out)

            gitlab_jobs[name] = (base, services, variables, job.get("parallelism"))

    return gitlab_jobs


def gen_required_suites() -> None:
    """Generate the list of test suites that need to be run."""
    from needs_testrun import extract_git_commit_selections
    from needs_testrun import for_each_testrun_needed as fetn
    from suitespec import get_suites

    all_jobs = convert_circleci_config()

    (GITLAB / "tests-gen.yml").write_text((GITLAB / "tests.yml").read_text())  # TODO: Delete this line

    suites = get_suites()
    required_suites = []

    fetn(
        suites=sorted(suites),
        action=lambda suite: required_suites.append(suite),
        git_selections=extract_git_commit_selections(os.getenv("CI_COMMIT_MESSAGE", "")),
    )

    if not required_suites:
        # Nothing to generate
        return

    with (GITLAB / "tests-gen.yml").open("a") as f:
        for suite in required_suites:
            if suite not in SUITES:
                continue

            if suite not in all_jobs:
                continue

            print_job(suite, *all_jobs[suite], file=f)

    # Generate the list of suites to run


# -----------------------------------------------------------------------------

# The code below is the boilerplate that makes the script work. There is
# generally no reason to modify it.

import logging  # noqa
import os  # noqa
import sys  # noqa
from argparse import ArgumentParser  # noqa
from pathlib import Path  # noqa
from time import monotonic_ns as time  # noqa

from ruamel.yaml import YAML  # noqa

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
LOGGER = logging.getLogger(__name__)

argp = ArgumentParser()
argp.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
args = argp.parse_args()
if args.verbose:
    LOGGER.setLevel(logging.INFO)

ROOT = Path(__file__).parents[1]
GITLAB = ROOT / ".gitlab"
TESTS = ROOT / "tests"
# Make the scripts and tests folders available for importing.
sys.path.append(str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests"))

LOGGER.info("Configuration generation steps:")
for name, func in dict(globals()).items():
    if name.startswith("gen_"):
        desc = func.__doc__.splitlines()[0]
        try:
            start = time()
            func()
            end = time()
            LOGGER.info("- %s: %s [took %dms]", name, desc, int((end - start) / 1e6))
        except Exception as e:
            LOGGER.error("- %s: %s [reason: %s]", name, desc, str(e), exc_info=True)
            has_error = True
