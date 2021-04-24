import glob
import json
import os

import pytest

from ddtrace.ext import ci


def _ci_fixtures():
    basepath = os.path.join(os.path.dirname(__file__), "fixtures", "ci")
    for filename in glob.glob(os.path.join(basepath, "*.json")):
        with open(filename) as fp:
            for i, item in enumerate(json.load(fp)):
                yield os.path.basename(filename)[:-5] + ":" + str(i), item[0], item[1]


def _updateenv(monkeypatch, env):
    for k, v in env.items():
        # monkeypatch logs a warning if values passed to setenv are not strings
        monkeypatch.setenv(str(k), str(v))


@pytest.mark.parametrize("name,environment,tags", _ci_fixtures())
def test_ci_providers(monkeypatch, name, environment, tags):
    _updateenv(monkeypatch, environment)
    assert tags == ci.tags(environment), "wrong tags in {0} for {1}".format(name, environment)
