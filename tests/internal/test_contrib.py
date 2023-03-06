import importlib
import os
from typing import List

# from packaging.specifiers import SpecifierSet
from pkg_resources import Requirement


def ignored_integrations():
    # type: () -> List[str]
    return ["aws_lambda", "dbapi", "boto"]


def test_spec():
    """Ensure the spec of each integration."""
    integrations = [
        f
        for f in os.listdir(os.path.join("ddtrace", "contrib"))
        if os.path.isdir(os.path.join("ddtrace", "contrib", f)) and not f.startswith("_")
    ]
    for i in sorted(integrations):
        mod = importlib.import_module("ddtrace.contrib.%s" % i)
        assert hasattr(mod, "_spec")
        for pkg in mod._spec["required_packages"]:
            assert "version" in pkg
            Requirement.parse(pkg["version"])
