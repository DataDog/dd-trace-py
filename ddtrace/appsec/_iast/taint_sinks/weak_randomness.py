from ..constants import VULN_WEAK_RANDOMNESS
from ._base import VulnerabilityBase


class WeakRandomness(VulnerabilityBase):
    vulnerability_type = VULN_WEAK_RANDOMNESS
