"""WAF input/output types shared by both libddwaf backends, without loading the library."""

from collections.abc import Mapping
from collections.abc import Sequence
from enum import Enum
from typing import Union


DDWafInputType = Union[None, int, float, str, Sequence["DDWafInputType"], Mapping[str, "DDWafInputType"]]

DDWafOutputType = Union[None, int, float, str, list["DDWafOutputType"], dict[str, "DDWafOutputType"]]


class DDWafSqlTokenizer(str, Enum):
    GENERIC = "generic"
    MYSQL = "mysql"
    ORACLE = "oracle"
    POSTGRESQL = "postgresql"
    SQLITE = "sqlite"
