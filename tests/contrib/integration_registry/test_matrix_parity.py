from collections import Counter
from pathlib import Path
import re

import pytest
import yaml

from tests.environment import TestEnvironment as Environment
from tests.lock import match_riot_seed_locks
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
def test_declarative_matrix_is_covered_by_riot(suite, riot_environments):
    config = _suite_config(suite)
    matrix_environments = expand_suite_matrix(suite, config, _ROOT_SPEC["matrix_defaults"], nightly=False)

    missing = Counter(map(_normalized, matrix_environments)) - Counter(map(_normalized, riot_environments[suite]))
    assert not missing


@pytest.mark.parametrize("suite", _SUITES)
def test_each_declarative_environment_maps_to_existing_riot_lock(suite, riot_environments):
    config = _suite_config(suite)
    environments = expand_suite_matrix(suite, config, _ROOT_SPEC["matrix_defaults"], nightly=False)
    seeds = match_riot_seed_locks(environments)

    assert set(seeds) == {(environment.suite, environment.id) for environment in environments}
    riot_by_lock = {environment.lockfile: environment for environment in riot_environments[suite]}
    for environment in environments:
        assert environment.lockfile is not None
        assert environment.lockfile.name == f"{environment.id}.txt"
        seed = seeds[(environment.suite, environment.id)]
        assert re.fullmatch(r"[0-9a-f]{7}\.txt", seed.name)
        assert (_ROOT / seed).is_file()
        assert seed in riot_by_lock
        assert _normalized(environment) == _normalized(riot_by_lock[seed])


@pytest.mark.parametrize("suite", _SUITES)
def test_declarative_locks_copy_riot_contents(suite):
    config = _suite_config(suite)
    matrix_environments = expand_suite_matrix(suite, config, _ROOT_SPEC["matrix_defaults"], nightly=False)

    seeds = match_riot_seed_locks(matrix_environments)
    for environment in matrix_environments:
        assert environment.lockfile is not None
        seed = seeds[(environment.suite, environment.id)]
        assert (_ROOT / environment.lockfile).read_bytes() == (_ROOT / seed).read_bytes()
