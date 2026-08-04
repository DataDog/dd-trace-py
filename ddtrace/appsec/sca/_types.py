from typing import TypedDict


class CveTarget(TypedDict):
    target: str
    dependency_name: str
    cve_ids: list[str]
