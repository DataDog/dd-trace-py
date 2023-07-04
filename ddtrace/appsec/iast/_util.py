import re
import string
import sys
from typing import TYPE_CHECKING

from ddtrace.internal.logger import get_logger
from ddtrace.settings import _config as config


if TYPE_CHECKING:
    from typing import Any
    from typing import Iterable
    from typing import List
    from typing import Tuple

    from ddtrace.appsec.iast.reporter import Evidence


def _is_python_version_supported():  # type: () -> bool
    # IAST supports Python versions 3.6 to 3.11
    return (3, 6, 0) <= sys.version_info < (3, 12, 0)


def _is_iast_enabled():  # type: () -> bool
    if not config._iast_enabled:
        return False

    if not _is_python_version_supported():
        log = get_logger(__name__)
        log.info("IAST is not compatible with the current Python version")
        return False

    return True


# JJJ TODO:
# - configuration.rst
# - flask tests
# - catching test checking the cache member calling report() with a span

# Used to cache the compiled regular expression
_SOURCE_NAME_SCRUB = None
_SOURCE_VALUE_SCRUB = None


def _has_to_scrub(s):  # type: (str) -> bool
    global _SOURCE_NAME_SCRUB
    global _SOURCE_VALUE_SCRUB

    if _SOURCE_NAME_SCRUB is None:
        _SOURCE_NAME_SCRUB = re.compile(config._iast_redaction_name_pattern)
        _SOURCE_VALUE_SCRUB = re.compile(config._iast_redaction_value_pattern)

    return _SOURCE_NAME_SCRUB.match(s) is not None or _SOURCE_VALUE_SCRUB.match(s) is not None


_REPLACEMENTS = string.ascii_letters
_LEN_REPLACEMENTS = len(_REPLACEMENTS)


def _scrub(s, has_range=False):  # type: (str, bool) -> str
    if has_range:
        return "".join([_REPLACEMENTS[i % _LEN_REPLACEMENTS] for i in range(len(s))])
    return "*" * len(s)


def _is_evidence_value_parts(value):  # type: (Any) -> bool
    return isinstance(value, (set, list))


def _scrub_value_part(evidence):  # type: (Evidence) -> dict
    if _is_evidence_value_parts(evidence):
        evidence_value = evidence["value"]
        is_parts = True
        has_range = "source" in evidence
    else:
        evidence_value = evidence
        is_parts = False
        has_range = False

    def return_wrap(evidence_value):
        if is_parts:
            evidence["value"] = evidence_value
            return evidence
        return evidence_value

    if not _has_to_scrub(evidence_value):
        return return_wrap(evidence_value)

    return return_wrap(_scrub(evidence_value, has_range=has_range))


def _scrub_get_tokens_positions(text, tokens):
    # type: (str, Iterable[str]) -> List[Tuple[int, int]]
    token_positions = []

    for token in tokens:
        position = text.find(token)
        if position != -1:
            token_positions.append((position, position + len(token)))

    token_positions.sort()
    return token_positions
