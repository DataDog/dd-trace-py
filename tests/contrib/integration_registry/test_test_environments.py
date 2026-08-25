from pathlib import Path
import re

import pytest

from tests.suitespec import LOCK_PLATFORM
from tests.suitespec import MatrixError
from tests.suitespec import expand_suite_matrix
from tests.suitespec import get_test_environments


DEFAULT_PYTHON = ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]
DEFAULTS = {
    "python": DEFAULT_PYTHON,
    "dependencies": [
        "pytest",
        "requests<3",
        "pip==26.0.1; python_version < '3.10'",
        "pip==26.2.1; python_version >= '3.10'",
    ],
    "env": {"SHARED": "yes", "OVERRIDE": "base"},
}


def _expand(matrix):
    return expand_suite_matrix("contrib::example", {"matrix": matrix}, DEFAULTS, nightly=False)


def test_expands_only_variants_and_python():
    environments = _expand(
        {
            "command": "pytest shared",
            "dependencies": ["urllib3"],
            "env": {"MATRIX": "yes"},
            "variants": [
                {
                    "name": "legacy",
                    "python": ["3.9"],
                    "dependencies": ["requests==2.25"],
                    "env": {"OVERRIDE": "variant"},
                    "command": "pytest legacy",
                    "integration": "requests",
                    "runs": [{"command": "pytest first"}, {"command": "pytest second"}],
                },
                {"name": "latest", "dependencies": ["requests"]},
            ],
        }
    )

    assert [(environment.variant_name, environment.python) for environment in environments] == [
        ("legacy", "3.9"),
        *[("latest", python) for python in DEFAULT_PYTHON],
    ]
    assert all(re.fullmatch(r"[0-9a-f]{12}", environment.hash) for environment in environments)
    legacy = environments[0]
    assert legacy.integration_name == "requests"
    assert legacy.direct_dependencies == (
        "pytest",
        "pip==26.0.1; python_version < '3.10'",
        "pip==26.2.1; python_version >= '3.10'",
        "urllib3",
        "requests==2.25",
    )
    assert [run.command for run in legacy.runs] == ["pytest first", "pytest second"]
    assert legacy.runs[0].environment == {"MATRIX": "yes", "OVERRIDE": "variant", "SHARED": "yes"}


@pytest.mark.parametrize(
    "matrix, message",
    [
        ({"command": "pytest", "axes": {}}, "legacy matrix fields"),
        ({"command": "pytest", "platform": "macos"}, "unknown fields"),
        ({"command": "pytest", "variants": []}, "must not be empty"),
        ({"command": "pytest", "variants": [{"python": ["3.9"]}]}, "needs a name"),
        (
            {"command": "pytest", "variants": [{"name": "same"}, {"name": "same"}]},
            "duplicate variant name",
        ),
        ({"command": "pytest", "python": DEFAULT_PYTHON}, "complete default Python range"),
    ],
)
def test_invalid_matrix_shapes_fail_fast(matrix, message):
    with pytest.raises(MatrixError, match=message):
        _expand(matrix)


def test_declared_environments_map_one_to_one_to_committed_linux_locks():
    environments = [
        environment
        for suite_environments in get_test_environments(nightly=False).values()
        for environment in suite_environments
    ]

    assert LOCK_PLATFORM == "linux"
    assert len(environments) == len({environment.hash for environment in environments})
    assert all(
        re.fullmatch(
            rf"[a-z0-9-]+--py{environment.python.replace('.', '')}--{environment.hash}\.txt",
            environment.lockfile.name,
        )
        for environment in environments
    )
    assert {environment.lockfile for environment in environments} == set(Path(".uv").glob("*.txt"))
