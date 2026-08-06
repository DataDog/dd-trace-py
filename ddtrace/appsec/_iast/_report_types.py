from typing import TypedDict


class EvidenceData(TypedDict, total=False):
    dialect: str
    value: str
    valueParts: list[object]


class LocationData(TypedDict):
    path: str
    line: int


class VulnerabilityData(TypedDict):
    type: str
    evidence: EvidenceData
    location: LocationData
    hash: int


class IastReportData(TypedDict):
    vulnerabilities: list[VulnerabilityData]
