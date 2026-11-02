from typing import Optional

from ..constants import VULN_WEAK_RANDOMNESS
from ._base import TEXT_TYPES
from ._base import VulnerabilityBase


class WeakRandomness(VulnerabilityBase):
    vulnerability_type = VULN_WEAK_RANDOMNESS

    @classmethod
    def report(cls, evidence_value: Optional[TEXT_TYPES] = None, sources: object = None) -> None:
        super().report(evidence_value=evidence_value)
