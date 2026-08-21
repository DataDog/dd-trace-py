from pathlib import Path

import pytest
import yaml

from tests.internal.riot_seed_locks import RIOT_SEED_LOCKS
from tests.riot_adapter import load_riot_test_environments


pytest.importorskip("riot")
_ROOT = Path(__file__).parents[3]
_ROOT_SPEC = yaml.safe_load((_ROOT / "tests" / "suitespec.yml").read_text())
_CONTRIB_SPEC = yaml.safe_load((_ROOT / "tests" / "contrib" / "suitespec.yml").read_text())
_UV_SUITES = (
    "contrib::flask",
    "contrib::aiohttp",
    "contrib::aiohttp_jinja2",
    "tracer",
    "contrib::requests",
    "contrib::subprocess",
)


def _suite_config(suite):
    if suite.startswith("contrib::"):
        name = suite.removeprefix("contrib::")
        config = dict(_CONTRIB_SPEC["suites"][name])
        config.setdefault("pattern", name)
        return config
    return _ROOT_SPEC["suites"][suite]


@pytest.mark.parametrize("suite", _UV_SUITES)
def test_uv_migrated_suites_have_no_riot_environment_or_seed_lock(suite):
    environments = load_riot_test_environments({suite: _suite_config(suite)})

    assert environments[suite] == ()
    assert suite not in RIOT_SEED_LOCKS
