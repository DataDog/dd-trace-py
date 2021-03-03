from ddtrace.compat import parse
from ddtrace.vendor.dogstatsd import DogStatsd


def parse_dogstatsd_url(url):
    if url is None:
        return

    # url can be either of the form `udp://<host>:<port>` or `unix://<path>`
    # also support without url scheme included
    if url.startswith("/"):
        url = "unix://" + url
    elif "://" not in url:
        url = "udp://" + url

    parsed = parse.urlparse(url)

    if parsed.scheme == "unix":
        return dict(socket_path=parsed.path)
    elif parsed.scheme == "udp":
        return dict(host=parsed.hostname, port=parsed.port)
    else:
        raise ValueError("Unknown scheme `%s` for DogStatsD URL `{}`".format(parsed.scheme))


def get_dogstatsd_client(dogstatsd_url):
    if not dogstatsd_url:
        return

    dogstatsd_kwargs = parse_dogstatsd_url(dogstatsd_url)
    return DogStatsd(**dogstatsd_kwargs)
