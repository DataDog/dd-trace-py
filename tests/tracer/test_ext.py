import pytest

from ddtrace.ext import aws
from ddtrace.ext import ci
from ddtrace.ext import git
from ddtrace.ext import provider


def test_flatten_dict():
    """Ensure that flattening of a nested dict results in a normalized, 1-level dict"""
    d = dict(A=1, B=2, C=dict(A=3, B=4, C=dict(A=5, B=6)))
    e = dict(A=1, B=2, C_A=3, C_B=4, C_C_A=5, C_C_B=6)
    assert aws._flatten_dict(d, sep="_") == e


AZURE = [
    (
        {
            "TF_BUILD": "true",
            "BUILD_DEFINITIONNAME": "name",
            "BUILD_SOURCEVERSION": "0000000000000000000000000000000000000000",
        },
        {
            ci.PROVIDER_NAME: "azurepipelines",
            ci.PIPELINE_NAME: "name",
            git.COMMIT_SHA: "0000000000000000000000000000000000000000",
            "git.commit_sha": "0000000000000000000000000000000000000000",  # deprecated field
        },
    ),
]


@pytest.mark.parametrize("environment,tags", AZURE)
def test_ci_providers(environment, tags):
    assert tags == provider.Provider.from_env(environment).astags()
