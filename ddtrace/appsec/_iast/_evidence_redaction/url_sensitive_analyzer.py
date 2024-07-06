import re

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
AUTHORITY = r"^(?:[^:]+:)?//([^@]+)@"
QUERY_FRAGMENT = r"[?#&]([^=&;]+)=([^?#&]+)"
pattern = re.compile(f"({AUTHORITY})|({QUERY_FRAGMENT})", re.IGNORECASE | re.MULTILINE)


def url_sensitive_analyzer(evidence, name_pattern=None, value_pattern=None):
    try:
        ranges = []
        regex_result = pattern.search(evidence.value)

        while regex_result is not None:
            if isinstance(regex_result.group(1), str):
                end = regex_result.start() + (len(regex_result.group(0)) - 1)
                start = end - len(regex_result.group(1))
                ranges.append({"start": start, "end": end})

            if isinstance(regex_result.group(3), str):
                end = regex_result.start() + len(regex_result.group(0))
                start = end - len(regex_result.group(3))
                ranges.append({"start": start, "end": end})

            regex_result = pattern.search(evidence.value, regex_result.end())

        return ranges
    except Exception as e:
        log.debug(e)

    return []
