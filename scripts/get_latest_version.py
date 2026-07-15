import pathlib
import sys

import requests


sys.path.insert(0, str(pathlib.Path(__file__).parent / "integration_registry"))
from mappings import DEPENDENCY_TO_INTEGRATION_MAPPING  # noqa: E402
from mappings import INTEGRATION_TO_DEPENDENCY_MAPPING  # noqa: E402


def normalize_to_pypi_name(name: str) -> str:
    """Resolve a riot venv / integration name to its PyPI project name.

    PyPI already normalizes ``-``/``_``/case per PEP 503, so the only cases
    that actually need translating are the ones where the venv name and the
    PyPI project name are different words (e.g. ``asyncio`` -> ``pytest-asyncio``,
    ``azure_durable_functions`` -> ``azure-functions-durable``). Those live in
    ``scripts/integration_registry/mappings.py``.
    """
    key = name.lower()

    if key in DEPENDENCY_TO_INTEGRATION_MAPPING:
        return key

    deps = INTEGRATION_TO_DEPENDENCY_MAPPING.get(key)
    if deps and len(deps) == 1:
        return next(iter(deps))
    if deps and len(deps) > 1:
        raise SystemExit(f"Venv '{name}' maps to multiple PyPI packages: {sorted(deps)}. Pass the exact PyPI name.")

    return key


def get_latest_version(package_name):
    response = requests.get(f"https://pypi.org/pypi/{package_name}/json", timeout=10)
    data = response.json()
    return data["info"]["version"]


package_name = normalize_to_pypi_name(sys.argv[1])
latest_version = get_latest_version(package_name)
print(f"{latest_version}")
