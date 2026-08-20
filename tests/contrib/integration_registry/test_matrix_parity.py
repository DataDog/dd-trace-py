from collections import Counter
from pathlib import Path
import re

import pytest
import yaml

from tests.environment import TestEnvironment as Environment
from tests.matrix import expand_suite_matrix
from tests.riot_adapter import load_riot_test_environments


pytest.importorskip("riot")
_ROOT = Path(__file__).parents[3]
_ROOT_SPEC = yaml.safe_load((_ROOT / "tests" / "suitespec.yml").read_text())
_CONTRIB_SPEC = yaml.safe_load((_ROOT / "tests" / "contrib" / "suitespec.yml").read_text())
_SUITES = (
    "contrib::requests",
    "contrib::flask",
    "contrib::aiohttp",
    "contrib::aiohttp_jinja2",
    "tracer",
    "contrib::subprocess",
)


def _requirement_name(requirement):
    return re.match(r"^[A-Za-z0-9_.-]+", requirement).group(0).lower().replace("_", "-")


def _normalized(environment: Environment):
    dependencies = tuple(
        sorted({_requirement_name(item): item.lower() for item in environment.direct_dependencies}.items())
    )
    runs = tuple(sorted((" ".join(run.command.split()), tuple(sorted(run.env))) for run in environment.runs))
    return (
        environment.suite,
        environment.name,
        environment.python,
        dependencies,
        runs,
        tuple(sorted(environment.env)),
        environment.services,
        environment.snapshot,
        environment.retry,
        environment.timeout,
        environment.parallelism,
        environment.environments_per_job,
        environment.gpu,
        environment.skip_pip_cache,
    )


def _suite_config(suite):
    if suite.startswith("contrib::"):
        name = suite.removeprefix("contrib::")
        config = dict(_CONTRIB_SPEC["suites"][name])
        config.setdefault("pattern", name)
        return config
    return _ROOT_SPEC["suites"][suite]


@pytest.fixture(scope="module")
def riot_environments():
    return load_riot_test_environments({suite: _suite_config(suite) for suite in _SUITES})


@pytest.mark.parametrize("suite", _SUITES)
def test_declarative_matrix_matches_riot(suite, riot_environments):
    config = _suite_config(suite)
    matrix_environments = expand_suite_matrix(suite, config, _ROOT_SPEC["matrix_defaults"], nightly=False)

    assert Counter(map(_normalized, matrix_environments)) == Counter(map(_normalized, riot_environments[suite]))
