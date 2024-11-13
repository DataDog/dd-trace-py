# This script is used to generate the GitLab dynamic config file in
# .gitlab/tests.yml.
#
# To add new configuration manipulations that are based on top of the template
# file in .gitlab/tests.yml, add a function named gen_<name> to this
# file. The function will be called automatically when this script is run.

from dataclasses import dataclass
import typing as t


@dataclass
class JobSpec:
    name: str
    runner: str
    is_snapshot: bool = False
    services: t.Optional[t.Set[str]] = None
    env: t.Optional[t.Dict[str, str]] = None
    parallelism: t.Optional[int] = None
    retry: t.Optional[int] = None
    timeout: t.Optional[int] = None

    def __str__(self) -> str:
        lines = []
        base = f".test_base_{self.runner}"
        if self.is_snapshot:
            base += "_snapshot"

        lines.append(f"{self.name}:")
        lines.append(f"  extends: {base}")

        if self.services:
            lines.append("  services:")

            _services = [f"!reference [.services, {_}]" for _ in self.services]
            if self.is_snapshot:
                _services.insert(0, f"!reference [{base}, services]")

            for service in _services:
                lines.append(f"    - {service}")

        if self.env:
            lines.append("  variables:")
            for key, value in self.env.items():
                lines.append(f"    {key}: {value}")

        if self.parallelism is not None:
            lines.append(f"  parallel: {self.parallelism}")

        if self.retry is not None:
            lines.append(f"  retry: {self.retry}")

        if self.timeout is not None:
            lines.append(f"  timeout: {self.timeout}")

        return "\n".join(lines)


def collect_jobspecs() -> dict:
    # Recursively search for jobspec.yml in TESTS
    jobspecs = {}
    for js in TESTS.rglob("jobspec.yml"):
        with YAML() as yaml:
            LOGGER.info("Loading jobspecs from %s", js)
            jobspecs.update(yaml.load(js))

    return jobspecs


def gen_required_suites() -> None:
    """Generate the list of test suites that need to be run."""
    from needs_testrun import extract_git_commit_selections
    from needs_testrun import for_each_testrun_needed as fetn
    from suitespec import get_suites

    jobspecs = collect_jobspecs()

    suites = get_suites()
    required_suites = []

    for suite in suites:
        if suite not in jobspecs:
            print(f"WARNING: Suite {suite} has no jobspec", file=sys.stderr)
            continue

    for job in jobspecs:
        if job not in suites:
            print(f"WARNING: Job {job} has no suitespec", file=sys.stderr)
            continue

    fetn(
        suites=sorted(suites),
        action=lambda suite: required_suites.append(suite),
        git_selections=extract_git_commit_selections(os.getenv("CI_COMMIT_MESSAGE", "")),
    )

    # Copy the template file
    (GITLAB / "tests-gen.yml").write_text(
        (GITLAB / "tests.yml").read_text().replace(r"{{services.yml}}", (GITLAB / "services.yml").read_text())
    )

    # Generate the list of suites to run
    with (GITLAB / "tests-gen.yml").open("a") as f:
        for suite in required_suites:
            if suite not in jobspecs:
                print(f"Suite {suite} not found in jobspec", file=sys.stderr)
                continue

            print(str(JobSpec(suite, **jobspecs[suite])), file=f)


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
