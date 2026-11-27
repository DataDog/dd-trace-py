import bisect
import re
from typing import Optional

from ddtrace.appsec._iast._report_types import Evidence
from ddtrace.appsec._iast.constants import DBAPI_MARIADB
from ddtrace.appsec._iast.constants import DBAPI_MYSQL
from ddtrace.appsec._iast.constants import DBAPI_MYSQLDB
from ddtrace.appsec._iast.constants import DBAPI_PSYCOPG
from ddtrace.appsec._iast.constants import DBAPI_PYMYSQL
from ddtrace.appsec._iast.constants import DBAPI_SQLITE
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


STRING_LITERAL = r"'(?:''|[^'])*'"
POSTGRESQL_DOLLAR_TAG = r"\$(?:[A-Za-z_\u0080-\U0010FFFF][A-Za-z0-9_\u0080-\U0010FFFF]*)?\$"
_POSTGRESQL_DOLLAR_TAG_LOOKAHEAD = re.compile(rf"(?=({POSTGRESQL_DOLLAR_TAG}))")
MYSQL_STRING_LITERAL = r'"(?:\\\\"|[^"])*"|\'(?:\\\\\'|[^\'])*\''
LINE_COMMENT = r"--.*$"
BLOCK_COMMENT = r"/\*[\s\S]*?\*/"
EXPONENT = r"(?:E[-+]?\d+[fd]?\b)?"
DECIMAL_NUMBER = r"(?:(?<!\w)\d+(?:\.\d*)?|\.\d+)"
HEX_NUMBER = r"x'[0-9a-f]+'|0x[0-9a-f]+"
BIN_NUMBER = r"b'[0-9a-f]+'|0b[0-9a-f]+"
NUMERIC_LITERAL = rf"[-+]?(?:{HEX_NUMBER}|{BIN_NUMBER}|{DECIMAL_NUMBER}{EXPONENT})"

patterns = {
    DBAPI_MYSQL: re.compile(
        f"({NUMERIC_LITERAL})|({MYSQL_STRING_LITERAL})|({LINE_COMMENT})|({BLOCK_COMMENT})", re.IGNORECASE | re.MULTILINE
    ),
    DBAPI_PSYCOPG: re.compile(
        f"({NUMERIC_LITERAL})|({POSTGRESQL_DOLLAR_TAG})|({STRING_LITERAL})|({LINE_COMMENT})|({BLOCK_COMMENT})",
        re.IGNORECASE | re.MULTILINE,
    ),
}
patterns[DBAPI_SQLITE] = patterns[DBAPI_MYSQL]
patterns[DBAPI_MARIADB] = patterns[DBAPI_MYSQL]
patterns[DBAPI_PYMYSQL] = patterns[DBAPI_MYSQL]
patterns[DBAPI_MYSQLDB] = patterns[DBAPI_MYSQL]


def _build_dollar_tag_positions(sql: str) -> dict[str, list[int]]:
    positions: dict[str, list[int]] = {}
    for match in _POSTGRESQL_DOLLAR_TAG_LOOKAHEAD.finditer(sql):
        tag = match.group(1)
        positions.setdefault(tag, []).append(match.start())
    return positions


def _is_pg_identifier_char(ch: str) -> bool:
    return ch in "$_" or not ch.isascii() or ch.isalnum()


def sql_sensitive_analyzer(
    evidence: Evidence,
    name_pattern: re.Pattern[str],
    value_pattern: re.Pattern[str],
    query_string_pattern: Optional[re.Pattern[bytes]] = None,
) -> list[dict[str, int]]:
    """
    SQL sensitive analyzer for evidence redaction.

    Args:
    - evidence: The evidence to analyze
    - name_pattern: Pattern for matching sensitive names
    - value_pattern: Pattern for matching sensitive values
    - query_string_pattern: Query string obfuscation pattern (unused in SQL analyzer)

    Returns:
    - list: List of sensitive ranges to redact
    """
    evidence_value = evidence.value
    if evidence_value is None:
        return []

    pattern = patterns.get(evidence.dialect or DBAPI_MYSQL, patterns[DBAPI_MYSQL])
    tokens: list[dict[str, int]] = []
    dollar_tag_positions: Optional[dict[str, list[int]]] = None
    search_from = 0
    token_boundary = 0

    regex_result = pattern.search(evidence_value, search_from)
    while regex_result is not None:
        start = regex_result.start()
        end = regex_result.end()
        start_char = evidence_value[start]
        if start_char == "$":
            # In PostgreSQL, '$' inside an unquoted identifier (e.g. col$t$) does not start a dollar-quoted literal.
            if start > token_boundary and _is_pg_identifier_char(evidence_value[start - 1]):
                search_from = start + 1
                regex_result = pattern.search(evidence_value, search_from)
                continue
            tag = evidence_value[start:end]
            if dollar_tag_positions is None:
                dollar_tag_positions = _build_dollar_tag_positions(evidence_value)
            positions = dollar_tag_positions.get(tag)
            close_pos = -1
            if positions:
                idx = bisect.bisect_left(positions, end)
                if idx < len(positions):
                    close_pos = positions[idx]
            if close_pos < 0:
                search_from = token_boundary = end
                regex_result = pattern.search(evidence_value, search_from)
                continue
            tokens.append({"start": end, "end": close_pos})
            search_from = token_boundary = close_pos + len(tag)
            regex_result = pattern.search(evidence_value, search_from)
            continue
        if start_char == "'" or start_char == '"':
            start += 1
            end -= 1
        elif end > start + 1:
            next_char = evidence_value[start + 1]
            if start_char == "/" and next_char == "*":
                start += 2
                end -= 2
            elif start_char == "-" and start_char == next_char:
                start += 2
            elif start_char.lower() == "q" and next_char == "'":
                start += 3
                end -= 2
        tokens.append({"start": start, "end": end})
        search_from = token_boundary = regex_result.end()
        regex_result = pattern.search(evidence_value, search_from)
    return tokens
