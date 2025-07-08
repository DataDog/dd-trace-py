from ..constants import VULN_WEAK_RANDOMNESS
from ._base import VulnerabilityBase


class WeakRandomness(VulnerabilityBase):
    vulnerability_type = VULN_WEAK_RANDOMNESS

    @classmethod
    def report(cls, evidence_value=None, sources=None):
        super(WeakRandomness, cls).report(evidence_value=evidence_value)
