import re


try:
    from urllib.parse import urlsplit
    from urllib.parse import urlunsplit
except ImportError:
    from urlparse import urlsplit
    from urlparse import urlunsplit

SCP_REGEXP = re.compile("^[a-z0-9_]+@([a-z0-9._-]+):(.*)$", re.IGNORECASE)


def __remove_suffix(s, suffix):
    if s.endswith(suffix):
        return s[: -len(suffix)]
    else:
        return s


def normalize_repository_url(url):
    scheme = ""
    hostname = ""
    port = None
    path = ""

    match = SCP_REGEXP.match(url)
    if match:
        # Check URLs like "git@github.com:user/project.git",
        scheme = "https"
        hostname = match.group(1)
        path = "/" + match.group(2)
    else:
        u = urlsplit(url)
        if u.scheme == "" and u.hostname is None:
            # Try to add a scheme.
            u = urlsplit("https://" + url)  # Default to HTTPS.
            if u.hostname is None:
                return ""

        scheme = u.scheme
        hostname = u.hostname
        port = u.port
        path = u.path

        if scheme not in ("http", "https", "git", "ssh"):
            return ""

        if not scheme.startswith("http"):
            scheme = "https"  # Default to HTTPS.
            port = None

    path = __remove_suffix(path, ".git/")
    path = __remove_suffix(path, ".git")

    netloc = hostname
    if port is not None:
        netloc += ":" + str(port)

    return urlunsplit((scheme, netloc, path, "", ""))
