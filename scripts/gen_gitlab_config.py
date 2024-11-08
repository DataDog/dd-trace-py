# This script is used to generate the CircleCI dynamic config file in
# .circleci/config.gen.yml.
#
# To add new configuration manipulations that are based on top of the template
# file in .circleci/config.templ.yml, add a function named gen_<name> to this
# file. The function will be called automatically when this script is run.

SUITES = {
    "debugger",
}


def print_job(
    name, runner, is_snapshot, services=None, env=None, parallelism=None, retry=None, timeout=None, file=None
):
    base = f".test_base_{runner}"
    if is_snapshot:
        base += "_snapshot"

    print(f"{name}:", file=file)

    print(f"  extends: {base}", file=file)

    if services:
        print("  services:", file=file)

        _services = [f"!reference [.services, {_}]" for _ in services]
        if is_snapshot:
            _services.insert(0, "!reference [.test_base_riot_snapshot, services]")

        for service in _services:
            print(f"    - {service}", file=file)

    if parallelism is not None:
        print(f"  parallel: {parallelism}", file=file)

    if env is not None:
        print("  variables:", file=file)
        for key, value in env.items():
            print(f"    {key}: {value}", file=file)

    if retry is not None:
        print(f"  retry: {retry}", file=file)

    if timeout is not None:
        print(f"  timeout: {timeout}", file=file)

    print(file=file)


# TODO: delete this function
def convert_circleci_config():
    gitlab_jobs = {}
    jobs = {}

    SERVICE_VARIABLES = {
        "postgres": {"TEST_POSTGRES_HOST": "postgres"},
        "redis": {"TEST_REDIS_HOST": "redis"},
        "memcached": {"TEST_MEMCACHED_HOST": "memcached"},
    }

    CONFIG_TEMPLATE_FILE = ROOT / ".circleci" / "config.templ.yml"
    with YAML(output=TESTS / "jobspec.yml") as yaml, (GITLAB / "alltests.yml").open("w") as out:
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
            services = set(run_test.get("docker_services", "").split())

            variables = {"SUITE_NAME": name if runner == "riot" else run_test["env"]}
            for service in services:
                variables.update(SERVICE_VARIABLES.get(service, {}))

            jobs[name] = {
                "runner": runner,
                "is_snapshot": is_snapshot,
                "services": services,
                "env": variables,
            }
            parallel = job.get("parallelism")
            if parallel is not None:
                jobs[name]["parallelism"] = parallel

            retry = job.get("retry")
            if retry is not None:
                jobs[name]["retry"] = retry

            base = (
                (".test_base_riot_snapshot" if is_snapshot else ".test_base_riot")
                if runner == "riot"
                else ".test_base_hatch"
            )

            print_job(name, runner, is_snapshot, services, variables, parallel, file=out)

            gitlab_jobs[name] = (base, services, variables, parallel)

        yaml.dump(jobs)

    return gitlab_jobs


def collect_jobspecs() -> dict:
    # Recursively search for jobspec.yml in TESTS
    jobspecs = {}
    for js in TESTS.rglob("jobspec.yml"):
        with YAML() as yaml:
            LOGGER.info("Loading jobspec from %s", js)
            jobspecs.update(yaml.load(js))

    return jobspecs


def gen_required_suites() -> None:
    """Generate the list of test suites that need to be run."""
    from needs_testrun import extract_git_commit_selections
    from needs_testrun import for_each_testrun_needed as fetn
    from suitespec import get_suites

    all_jobs = collect_jobspecs()

    (GITLAB / "tests-gen.yml").write_text(
        (GITLAB / "tests.yml").read_text().replace(r"{{services.yml}}", (GITLAB / "services.yml").read_text())
    )  # TODO: Delete this line

    suites = get_suites()
    required_suites = []

    for suite in suites:
        if suite not in all_jobs:
            print(f"WARNING: Suite {suite} has no jobspec", file=sys.stderr)
            continue

    for job in all_jobs:
        if job not in suites:
            print(f"WARNING: Job {job} has no suitespec", file=sys.stderr)
            continue

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
            if suite not in all_jobs:
                print(f"Suite {suite} not found in jobspec", file=sys.stderr)
                continue

            job_spec = all_jobs[suite]

            print_job(suite, **job_spec, file=f)

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
