import re
from typing import TYPE_CHECKING  # noqa:F401

from .. import oce
from ..constants import VULN_SQL_INJECTION
from ._base import VulnerabilityBase


_TEXT_TOKENS_REGEXP = re.compile(r"\b\w+\b")


@oce.register
class SqlInjection(VulnerabilityBase):
    vulnerability_type = VULN_SQL_INJECTION
