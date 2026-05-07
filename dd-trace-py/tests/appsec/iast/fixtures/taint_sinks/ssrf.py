"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes

This module provides thin wrappers around various HTTP client calls to exercise SSRF sinks.
"""


def pt_requests_get(url):
    """Trigger a requests.get call with the provided URL."""
    import requests
    from requests.exceptions import ConnectionError  # noqa: A004

    try:
        # label pt_requests_get
        return requests.get(url)
    except ConnectionError:
        pass


def pt_urllib3_poolmanager(url):
    """Trigger a urllib3.request call with the provided URL."""
    import urllib3
    from urllib3.exceptions import MaxRetryError

    try:
        http_poolmanager = urllib3.PoolManager(num_pools=1)
        # label pt_urllib3_poolmanager
        response = http_poolmanager.request("GET", url)
        http_poolmanager.clear()
        return response.data
    except MaxRetryError:
        pass


def pt_urllib3_request(url):
    """Trigger a urllib3.request call with the provided URL."""

    import urllib3
    from urllib3.exceptions import MaxRetryError

    try:
        # label pt_urllib3_request
        return urllib3.request(method="GET", url=url)
    except MaxRetryError:
        pass


def pt_httplib_request(url):
    """Trigger an http.client request using the provided URL as path."""

    import http.client

    conn = http.client.HTTPConnection("127.0.0.1")
    try:
        # label pt_httplib_request
        conn.request("GET", url)
        return conn.getresponse()
    except ConnectionError:
        pass


def pt_webbrowser_open(url):
    """Trigger a webbrowser.open call with the provided URL."""

    import webbrowser

    # label pt_webbrowser_open
    return webbrowser.open(url)


def pt_urllib_request(url):
    """Trigger a urllib.request.urlopen call with the provided URL."""
    from urllib.error import URLError
    import urllib.request

    try:
        # label pt_urllib_request
        return urllib.request.urlopen(url)
    except URLError:
        pass
