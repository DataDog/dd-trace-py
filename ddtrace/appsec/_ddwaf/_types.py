"""WAF input/output types and input limits."""

from collections.abc import Mapping
from collections.abc import Sequence
from enum import Enum
from typing import Union


DDWafInputType = Union[None, int, float, str, Sequence["DDWafInputType"], Mapping[str, "DDWafInputType"]]


class DDWafSqlTokenizer(str, Enum):
    GENERIC = "generic"
    MYSQL = "mysql"
    ORACLE = "oracle"
    POSTGRESQL = "postgresql"
    SQLITE = "sqlite"


DDWAF_MAX_STRING_LENGTH = 4096
DDWAF_MAX_CONTAINER_DEPTH = 20
