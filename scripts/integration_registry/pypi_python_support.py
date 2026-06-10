from functools import lru_cache
import json
import re
import urllib.request

from packaging.specifiers import InvalidSpecifier
from packaging.specifiers import SpecifierSet
from packaging.version import Version


PYTHON_VERSION_CLASSIFIER = re.compile(r"^Programming Language :: Python :: (3\.\d+)$")


@lru_cache(maxsize=None)
def _load_pypi_metadata(package: str, version: str) -> dict:
    url = f"https://pypi.org/pypi/{package}/{version}/json"

    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def _python_version_from_classifier(classifier: str) -> tuple[int, int] | None:
    match = PYTHON_VERSION_CLASSIFIER.match(classifier)
    if match is None:
        return None

    major, minor = match.group(1).split(".")
    return int(major), int(minor)


def _python_version_from_wheel_tag(python_version: str) -> tuple[int, int] | None:
    if not python_version.startswith("cp"):
        return None

    version = python_version.removeprefix("cp")
    if not version.isdigit():
        return None

    return int(version[0]), int(version[1:])


def _requires_python(data: dict) -> str | None:
    requires_python = data.get("info", {}).get("requires_python")
    if requires_python:
        return requires_python

    for release in data.get("urls", []):
        requires_python = release.get("requires_python")
        if requires_python:
            return requires_python

    return None


def _minimum_python_from_metadata(data: dict) -> tuple[int, int] | None:
    versions = set()
    for classifier in data.get("info", {}).get("classifiers", []):
        python_version = _python_version_from_classifier(classifier)
        if python_version is not None:
            versions.add(python_version)

    for release in data.get("urls", []):
        python_version = _python_version_from_wheel_tag(release.get("python_version", ""))
        if python_version is not None:
            versions.add(python_version)

    return min(versions) if versions else None


@lru_cache(maxsize=None)
def compatible_python_versions(package: str, version: str, candidates: tuple[str, ...]) -> frozenset[str]:
    """Return candidate Python versions compatible with a PyPI package release."""
    data = _load_pypi_metadata(package, version)
    requires_python = _requires_python(data)

    if requires_python:
        try:
            specifier = SpecifierSet(requires_python)
        except InvalidSpecifier:
            pass
        else:
            return frozenset(candidate for candidate in candidates if Version(candidate) in specifier)

    minimum_python = _minimum_python_from_metadata(data)
    if minimum_python is None:
        return frozenset(candidates)

    return frozenset(
        candidate for candidate in candidates if tuple(int(part) for part in candidate.split(".")) >= minimum_python
    )
