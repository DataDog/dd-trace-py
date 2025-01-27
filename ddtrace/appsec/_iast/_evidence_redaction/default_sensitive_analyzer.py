from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def default_sensitive_analyzer(evidence, name_pattern, value_pattern):
    if name_pattern.search(evidence.value) or value_pattern.search(evidence.value):
        return [{"start": 0, "end": len(evidence.value)}]

    return []
