# This script is used to generate the CircleCI dynamic config file in
# .circleci/config.gen.yml.
#
# To add new configuration manipulations that are based on top of the template
# file in .circleci/config.templ.yml, add a function named gen_<name> to this
# file. The function will be called automatically when this script is run.

import typing as t


def gen_required_suites(template: dict, git_selections: list) -> None:
    """Generate the list of test suites that need to be run."""
    from needs_testrun import for_each_testrun_needed as fetn
    from suitespec import get_suites

    suites = get_suites()
    jobs = set(template["jobs"].keys())

    required_suites = template["requires_tests"]["requires"] = []
    fetn(
        suites=sorted(suites & jobs), action=lambda suite: required_suites.append(suite), git_selections=git_selections
    )

    if not required_suites:
        # Nothing to generate
        return

    jobs = template["workflows"]["test"]["jobs"]

    # Create the base venvs
    jobs.append("build_base_venvs")

    # Add the jobs
    requires_base_venvs = template["requires_base_venvs"]
    jobs.extend([{suite: requires_base_venvs} for suite in required_suites])

    # Collect coverage
    jobs.append({"coverage_report": template["requires_tests"]})


def gen_pre_checks(template: dict) -> None:
    """Generate the list of pre-checks that need to be run."""
    from needs_testrun import pr_matches_patterns

    def check(name: str, command: str, paths: t.Set[str]) -> None:
        if pr_matches_patterns(paths):
            template["jobs"]["pre_check"]["steps"].append({"run": {"name": name, "command": command}})

    check(
        name="Style",
        command="hatch run lint:style",
        paths={"*.py", "*.pyi", "hatch.toml", "pyproject.toml"},
    )
    check(
        name="Typing",
        command="hatch run lint:typing",
        paths={"*.py", "*.pyi", "hatch.toml"},
    )
    check(
        name="Security",
        command="hatch run lint:security",
        paths={"ddtrace/*", "hatch.toml"},
    )
    check(
        name="Run riotfile.py tests",
        command="hatch run lint:riot",
        paths={"riotfile.py", "hatch.toml"},
    )
    check(
        name="Style: Test snapshots",
        command="hatch run lint:fmt-snapshots && git diff --exit-code tests/snapshots hatch.toml",
        paths={"tests/snapshots/*", "hatch.toml"},
    )
    check(
        name="Run scripts/*.py tests",
        command="hatch run scripts:test",
        paths={"scripts/*.py", "scripts/mkwheelhouse", "scripts/run-test-suite", "tests/.suitespec.json"},
    )
    check(
        name="Validate suitespec JSON file",
        command="python -m tests.suitespec",
        paths={"tests/.suitespec.json", "tests/suitespec.py"},
    )


def gen_build_docs(template: dict) -> None:
    """Include the docs build step if the docs have changed."""
    from needs_testrun import pr_matches_patterns

    if pr_matches_patterns({"docs/*", "ddtrace/*", "scripts/docs", "releasenotes/*"}):
        template["workflows"]["test"]["jobs"].append({"build_docs": template["requires_pre_check"]})


def gen_slotscheck(template: dict) -> None:
    """Include the slotscheck if the Python source has changed."""
    from needs_testrun import pr_matches_patterns

    if pr_matches_patterns({"ddtrace/*.py", "hatch.toml"}):
        template["workflows"]["test"]["jobs"].append({"slotscheck": template["requires_pre_check"]})


def gen_conftests(template: dict) -> None:
    """Include the conftests if the Python conftest or tests/meta has changed."""
    from needs_testrun import pr_matches_patterns

    if pr_matches_patterns({"tests/*conftest.py", "tests/meta/*"}):
        template["workflows"]["test"]["jobs"].append({"conftests": template["requires_pre_check"]})


def gen_c_check(template: dict) -> None:
    """Include C code checks if C code has changed."""
    from needs_testrun import pr_matches_patterns

    if pr_matches_patterns({"*.c", "*.h", "*.cpp", "*.hpp", "*.cc", "*.hh"}):
        template["requires_pre_check"]["requires"].append("ccheck")
        template["requires_base_venvs"]["requires"].append("ccheck")
        template["workflows"]["test"]["jobs"].append("ccheck")


def extract_git_commit_selections(git_commit_message: str) -> dict:
    """Extract the selected suites from git commit message."""
    suites = set()
    for token in git_commit_message.split():
        if token.lower().startswith("circleci:"):
            suites.update(token[len("circleci:") :].lower().split(","))
    return list(sorted(suites))


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
CONFIG_TEMPLATE_FILE = ROOT / ".circleci" / "config.templ.yml"
CONFIG_GEN_FILE = ROOT / ".circleci" / "config.gen.yml"

# Make the scripts and tests folders available for importing.
sys.path.append(str(ROOT / "scripts"))
sys.path.append(str(ROOT / "tests"))


with YAML(output=CONFIG_GEN_FILE) as yaml:
    LOGGER.info("Loading configuration template from %s", CONFIG_TEMPLATE_FILE)
    config = yaml.load(CONFIG_TEMPLATE_FILE)
    git_commit_selections = extract_git_commit_selections(os.getenv("GIT_COMMIT_DESC"))

    has_error = False
    LOGGER.info("Configuration generation steps:")
    for name, func in dict(globals()).items():
        if name.startswith("gen_"):
            desc = func.__doc__.splitlines()[0]
            try:
                start = time()
                if name == "gen_required_suites":
                    func(config, git_commit_selections)
                else:
                    func(config)
                end = time()
                LOGGER.info("- %s: %s [took %dms]", name, desc, int((end - start) / 1e6))
            except Exception as e:
                LOGGER.error("- %s: %s [reason: %s]", name, desc, str(e), exc_info=True)
                has_error = True

    LOGGER.info("Writing generated configuration to %s", CONFIG_GEN_FILE)
    yaml.dump(config)

    sys.exit(has_error)
