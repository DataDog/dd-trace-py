from typing import TypedDict


class CveTarget(TypedDict):
    id: str
    dependency_name: str
    targets: list[str]


class CveDataEntry(CveTarget):
    package_versions: list[str]
