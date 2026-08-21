from pathlib import Path
import re
import types

from tests import riot_adapter


class FakeInstance:
    def __init__(
        self,
        *,
        name,
        environment_id,
        python,
        command,
        packages,
        env=None,
        parent=None,
    ):
        self.name = name
        self.short_hash = environment_id
        self.py = types.SimpleNamespace(_hint=python)
        self.command = command
        self.pkgs = packages
        self.env = env or {}
        self.parent = parent

    def matches_pattern(self, pattern: re.Pattern):
        return pattern.search(self.name) is not None


def test_riot_adapter_groups_execution_variants_and_inherited_dependencies():
    parent = FakeInstance(
        name=None,
        environment_id="parent",
        python="3.11",
        command=None,
        packages={"pytest": "", "requests": "~=2.25.0"},
    )
    instances = (
        FakeInstance(
            name="requests",
            environment_id="shared-dependencies",
            python="3.11",
            command="pytest tests/contrib/requests",
            packages={"requests-mock": ">=1.4"},
            parent=parent,
        ),
        FakeInstance(
            name="requests",
            environment_id="shared-dependencies",
            python="3.11",
            command="python tests/ddtrace_run.py pytest tests/contrib/requests_autopatch",
            packages={"requests-mock": ">=1.4"},
            env={"DD_SERVICE": "requests-app"},
            parent=parent,
        ),
    )
    result = riot_adapter.load_riot_test_environments(
        {
            "contrib::requests": {
                "pattern": "^requests$",
                "env": {"REDIS_HOST": "redis"},
                "services": ["redis"],
                "snapshot": True,
                "retry": 2,
            }
        },
        root=types.SimpleNamespace(instances=lambda: iter(instances)),
    )

    assert len(result["contrib::requests"]) == 1
    environment = result["contrib::requests"][0]
    assert environment.id == "shared-dependencies"
    assert environment.direct_dependencies == ("pytest", "requests~=2.25.0", "requests-mock>=1.4")
    assert [run.command for run in environment.runs] == [
        "pytest tests/contrib/requests",
        "python tests/ddtrace_run.py pytest tests/contrib/requests_autopatch",
    ]
    assert environment.runs[1].environment == {"DD_SERVICE": "requests-app"}
    assert environment.environment == {"REDIS_HOST": "redis"}
    assert environment.services == ("redis",)
    assert environment.snapshot is True
    assert environment.retry == 2
    assert environment.lockfile == Path(".riot/requirements/shared-dependencies.txt")
