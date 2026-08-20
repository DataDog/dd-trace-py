from pathlib import Path

import pytest
import yaml

from tests.matrix import MatrixError
from tests.matrix import expand_declared_matrices
from tests.matrix import expand_suite_matrix


_ROOT = Path(__file__).parents[2]


def test_matrix_expands_axes_filters_and_exceptional_includes():
    config = {
        "env": {"SUITE_SETTING": "enabled"},
        "services": ["redis"],
        "snapshot": True,
        "retry": 2,
        "venvs_per_job": 3,
        "matrix": {
            "python": ["3.11", "3.12"],
            "name": "example-alias",
            "dependencies": ["pytest", "shared==1"],
            "dependency_groups": ["test-common"],
            "command": "pytest {cmdargs} tests/example",
            "env": {"BASE": "1"},
            "axes": {
                "framework": {
                    "framework-1": {"python": ["3.11"], "dependencies": ["framework<2"]},
                    "framework-latest": {"dependencies": ["framework"]},
                },
                "transport": {
                    "sync": {"dependencies": ["transport==1"]},
                    "async": {
                        "dependencies": ["transport==2"],
                        "command": "pytest {cmdargs} tests/example_async",
                        "env": {"ASYNC": "1"},
                    },
                },
            },
            "exclude": [{"python": "3.11", "transport": "async"}],
            "include": [
                {
                    "python": "3.12",
                    "framework": "framework-1",
                    "transport": "sync",
                    "dependencies": ["compatibility-shim"],
                    "command": "pytest {cmdargs} tests/example_legacy",
                }
            ],
        },
    }

    environments = expand_suite_matrix("contrib::example", config, nightly=False)

    assert len(environments) == 5
    assert [environment.id for environment in environments] == [
        "example-alias-py311-framework-1-sync",
        "example-alias-py311-framework-latest-sync",
        "example-alias-py312-framework-latest-sync",
        "example-alias-py312-framework-latest-async",
        "example-alias-py312-framework-1-sync",
    ]
    exceptional = environments[-1]
    assert exceptional.direct_dependencies == (
        "pytest",
        "shared==1",
        "framework<2",
        "transport==1",
        "compatibility-shim",
    )
    assert exceptional.dependency_groups == ("test-common", "framework-1", "sync")
    assert exceptional.command == "pytest {cmdargs} tests/example_legacy"
    assert exceptional.environment == {"SUITE_SETTING": "enabled"}
    assert exceptional.services == ("redis",)
    assert exceptional.snapshot is True
    assert exceptional.retry == 2
    assert exceptional.environments_per_job == 3
    async_environment = environments[-2]
    assert async_environment.runs[0].environment == {"ASYNC": "1", "BASE": "1"}


def test_matrix_merges_multiple_commands_for_one_dependency_environment():
    config = {
        "matrix": {
            "python": ["3.12"],
            "command": "unused",
            "axes": {"framework": {"framework-latest": "framework"}},
            "exclude": [{"python": "3.12"}],
            "include": [
                {
                    "python": "3.12",
                    "framework": "framework-latest",
                    "command": "pytest tests/framework",
                },
                {
                    "python": "3.12",
                    "framework": "framework-latest",
                    "command": "pytest tests/framework_autopatch",
                    "env": {"AUTOPATCH": "1"},
                },
            ],
        }
    }

    environments = expand_suite_matrix("framework", config, nightly=False)

    assert len(environments) == 1
    assert environments[0].id == "framework-py312-framework-latest"
    assert [run.command for run in environments[0].runs] == [
        "pytest tests/framework",
        "pytest tests/framework_autopatch",
    ]
    assert environments[0].runs[1].environment == {"AUTOPATCH": "1"}


def test_matrix_applies_nightly_environment_without_changing_identity():
    config = {"matrix": {"python": ["3.12"], "command": "pytest", "nightly_env": {"NIGHTLY": "yes"}}}

    regular = expand_suite_matrix("example", config, {"env": {"BASE": "1"}}, nightly=False)
    nightly = expand_suite_matrix("example", config, {"env": {"BASE": "1"}}, nightly=True)

    assert regular[0].id == nightly[0].id == "example-py312"
    assert regular[0].runs[0].environment == {"BASE": "1"}
    assert nightly[0].runs[0].environment == {"BASE": "1", "NIGHTLY": "yes"}


def test_declared_requests_matrix_has_semantic_ids():
    root_spec = yaml.safe_load((_ROOT / "tests" / "suitespec.yml").read_text())
    contrib_spec = yaml.safe_load((_ROOT / "tests" / "contrib" / "suitespec.yml").read_text())
    matrices = expand_declared_matrices(
        {"contrib::requests": contrib_spec["suites"]["requests"]},
        root_spec["matrix_defaults"],
        nightly=False,
    )

    requests = matrices["contrib::requests"]
    assert len(requests) == 9
    assert requests[0].id == "requests-py39-requests-2-25"
    assert requests[-1].id == "requests-py314-requests-latest"
    assert requests[0].services == ("httpbin",)
    assert requests[0].snapshot is True


@pytest.mark.parametrize(
    "matrix, message",
    [
        ({"command": "pytest"}, "does not declare any Python versions"),
        ({"python": ["3.12"], "command": "pytest", "axes": {"empty": {}}}, "does not declare any options"),
        (
            {
                "python": ["3.12"],
                "command": "pytest",
                "axes": {"framework": {"latest": "framework"}},
                "include": [{"python": "3.12", "framework": "missing"}],
            },
            "unknown framework option",
        ),
    ],
)
def test_matrix_rejects_invalid_declarations(matrix, message):
    with pytest.raises(MatrixError, match=message):
        expand_suite_matrix("invalid", {"matrix": matrix}, nightly=False)
