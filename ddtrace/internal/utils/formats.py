import logging
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Text
from typing import TypeVar
from typing import Union

from ..compat import binary_type
from ..compat import ensure_text
from ..compat import stringify
from ..compat import text_type


VALUE_PLACEHOLDER = "?"
VALUE_MAX_LEN = 100
VALUE_TOO_LONG_MARK = "..."
CMD_MAX_LEN = 1000


T = TypeVar("T")

# Tags `key:value` must be separated by either comma or space
_TAGS_NOT_SEPARATED = re.compile(r":[^,\s]+:")

log = logging.getLogger(__name__)


def deep_getattr(obj, attr_string, default=None):
    # type: (Any, str, Optional[Any]) -> Optional[Any]
    """
    Returns the attribute of `obj` at the dotted path given by `attr_string`
    If no such attribute is reachable, returns `default`

    >>> deep_getattr(cass, 'cluster')
    <cassandra.cluster.Cluster object at 0xa20c350

    >>> deep_getattr(cass, 'cluster.metadata.partitioner')
    u'org.apache.cassandra.dht.Murmur3Partitioner'

    >>> deep_getattr(cass, 'i.dont.exist', default='default')
    'default'
    """
    attrs = attr_string.split(".")
    for attr in attrs:
        try:
            obj = getattr(obj, attr)
        except AttributeError:
            return default

    return obj


def asbool(value):
    # type: (Union[str, bool, None]) -> bool
    """Convert the given String to a boolean object.

    Accepted values are `True` and `1`.
    """
    if value is None:
        return False

    if isinstance(value, bool):
        return value

    return value.lower() in ("true", "1")


def parse_tags_str(tags_str):
    # type: (Optional[str]) -> Dict[str, str]
    """Parse a string of tags typically provided via environment variables.

    The expected string is of the form::
        "key1:value1,key2:value2"
        "key1:value1 key2:value2"

    :param tags_str: A string of the above form to parse tags from.
    :return: A dict containing the tags that were parsed.
    """
    parsed_tags = {}  # type: Dict[str, str]
    if not tags_str:
        return parsed_tags

    if _TAGS_NOT_SEPARATED.search(tags_str):
        log.error("Malformed tag string with tags not separated by comma or space '%s'.", tags_str)
        return parsed_tags

    # Identify separator based on which successfully identifies the correct
    # number of valid tags
    numtagseps = tags_str.count(":")
    for sep in [",", " "]:
        if sum(":" in _ for _ in tags_str.split(sep)) == numtagseps:
            break
    else:
        log.error(
            (
                "Failed to find separator for tag string: '%s'.\n"
                "Tag strings must be comma or space separated:\n"
                "  key1:value1,key2:value2\n"
                "  key1:value1 key2:value2"
            ),
            tags_str,
        )
        return parsed_tags

    for tag in tags_str.split(sep):
        try:
            key, value = tag.split(":", 1)

            # Validate the tag
            if key == "" or value == "" or value.endswith(":"):
                raise ValueError
        except ValueError:
            log.error(
                "Malformed tag in tag pair '%s' from tag string '%s'.",
                tag,
                tags_str,
            )
        else:
            parsed_tags[key] = value

    return parsed_tags


def stringify_cache_args(args):
    # type: (List[Any]) -> Text
    """Convert a list of arguments into a space concatenated string

    This function is useful to convert a list of cache keys
    into a resource name or tag value with a max size limit.
    """
    length = 0
    out = []  # type: List[Text]
    for arg in args:
        try:
            if isinstance(arg, (binary_type, text_type)):
                cmd = ensure_text(arg, errors="backslashreplace")
            else:
                cmd = stringify(arg)

            if len(cmd) > VALUE_MAX_LEN:
                cmd = cmd[:VALUE_MAX_LEN] + VALUE_TOO_LONG_MARK

            if length + len(cmd) > CMD_MAX_LEN:
                prefix = cmd[: CMD_MAX_LEN - length]
                out.append("%s%s" % (prefix, VALUE_TOO_LONG_MARK))
                break

            out.append(cmd)
            length += len(cmd)
        except Exception:
            out.append(VALUE_PLACEHOLDER)
            break

    return " ".join(out)
