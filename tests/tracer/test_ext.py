import glob
import os
import json

import pytest

from ddtrace.ext import aws
from ddtrace.ext import ci


def test_flatten_dict():
    """Ensure that flattening of a nested dict results in a normalized, 1-level dict"""
    d = dict(A=1, B=2, C=dict(A=3, B=4, C=dict(A=5, B=6)))
    e = dict(A=1, B=2, C_A=3, C_B=4, C_C_A=5, C_C_B=6)
    assert aws._flatten_dict(d, sep="_") == e


def _ci_fixtures():
    basepath = os.path.join(os.path.dirname(__file__), "fixtures", "ci")
    for filename in glob.glob(os.path.join(basepath, "*.json")):
        with open(filename) as fp:
            for i, item in enumerate(json.load(fp)):
                yield os.path.basename(filename)[:-5] + ":" + str(i), item[0], item[1]


def _updateenv(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize("name,environment,tags", _ci_fixtures())
def test_ci_providers(monkeypatch, name, environment, tags):
    _updateenv(monkeypatch, environment)
    assert tags == ci.tags(environment), "wrong tags in {0} for {1}".format(name, environment)
