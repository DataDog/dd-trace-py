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


def _ci_fixtures(*names):
    basepath = os.path.join(os.path.dirname(__file__), 'fixtures', 'ci')
    for name in names:
        with open(os.path.join(basepath, name + '.json')) as fp:
            for item in json.load(fp):
                yield name, item[0], item[1]


def _updateenv(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize("name,environment,tags", _ci_fixtures("appveyor", "azurepipelines", "bitbucket"))
def test_ci_providers(monkeypatch, name, environment, tags):
    _updateenv(monkeypatch, environment)
    assert tags == ci.tags(), (name, environment)
