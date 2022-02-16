import logging
import os
import re
from typing import Any
from typing import Dict
from typing import Optional
from typing import TypeVar
from typing import Union

from ...vendor.debtcollector import deprecate


T = TypeVar("T")

# Tags `key:value` must be separated by either comma or space
_TAGS_NOT_SEPARATED = re.compile(r":[^,\s]+:")

log = logging.getLogger(__name__)


def get_env(*parts, **kwargs):
    # type: (str, T) -> Union[str, T, None]
    """Retrieves environment variables value for the given integration. It must be used
    for consistency between integrations. The implementation is backward compatible
    with legacy nomenclature:

    * `DATADOG_` is a legacy prefix with lower priority
    * `DD_` environment variables have the highest priority
    * the environment variable is built concatenating `integration` and `variable`
      arguments
    * return `default` otherwise

    :param parts: environment variable parts that will be joined with ``_`` to generate the name
    :type parts: :obj:`str`
    :param kwargs: ``default`` is the only supported keyword argument which sets the default value
        if no environment variable is found
    :rtype: :obj:`str` | ``kwargs["default"]``
    :returns: The string environment variable value or the value of ``kwargs["default"]`` if not found
    """
    default = kwargs.get("default")

    key = "_".join(parts)
    key = key.upper()
    legacy_env = "DATADOG_{}".format(key)
    env = "DD_{}".format(key)

    value = os.getenv(env)
    legacy = os.getenv(legacy_env)
    if legacy:
        # Deprecation: `DATADOG_` variables are deprecated
        deprecate(
            "DATADOG_ is deprecated",
            message="Use `DD_` prefix instead",
            removal_version="1.0.0",
        )

    value = value or legacy
    return value if value else default


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
