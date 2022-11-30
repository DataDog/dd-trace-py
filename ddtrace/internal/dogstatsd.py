from typing import Optional

from ddtrace.internal.compat import parse
from ddtrace.vendor.dogstatsd import DogStatsd
from ddtrace.vendor.dogstatsd import base


def get_dogstatsd_client(url, namespace=None):
    # type: (str, Optional[str]) -> DogStatsd
    # url can be either of the form `udp://<host>:<port>` or `unix://<path>`
    # also support without url scheme included
    if url.startswith("/"):
        url = "unix://" + url
    elif "://" not in url:
        url = "udp://" + url

    parsed = parse.urlparse(url)

    if parsed.scheme == "unix":
        return DogStatsd(socket_path=parsed.path, namespace=namespace)
    elif parsed.scheme == "udp":
        return DogStatsd(
            host=parsed.hostname,
            port=base.DEFAULT_PORT if parsed.port is None else parsed.port,
            namespace=namespace,
        )

    raise ValueError("Unknown scheme `%s` for DogStatsD URL `{}`".format(parsed.scheme))
