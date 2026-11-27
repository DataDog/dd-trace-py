from ddtrace.appsec._iast.constants import MAX_REDACTION_VALUE_LENGTH
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def default_sensitive_analyzer(evidence, name_pattern, value_pattern):
    value = evidence.value
    if len(value) > MAX_REDACTION_VALUE_LENGTH or name_pattern.search(value) or value_pattern.search(value):
        return [{"start": 0, "end": len(value)}]

    return []
