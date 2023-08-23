from typing import Any


def sanitize_string(value):
    # type: (Any) -> str
    try:
        return six.ensure_str(value)
    except Exception:
        try:
            return str(value)
        except Exception:
            return "[error]({})".format(value.__class__.__name__)
