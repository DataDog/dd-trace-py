from typing import Any


def sanitize_string(value):
    # type: (Any) -> str
    if isinstance(value, str):
        return value
    try:
        # uncommon, so just check for `decode` instead of bytes etc
        return value.decode("utf-8", "ignore")
    except AttributeError:
        try:
            return str(value)
        except Exception:
            pass
    except Exception:
        pass

    # Any total failures will return an string with encoded error information
    return "[error]({})".format(value.__class__.__name__)
