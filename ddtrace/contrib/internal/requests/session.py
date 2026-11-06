import requests

from ddtrace._trace._registration import HTTP_CLIENT_SUBSCRIBERS


class TracedSession(requests.Session):
    """TracedSession is a requests' Session that is already traced.
    You can use it if you want a finer grained control for your
    HTTP clients.
    """

    def __init__(self) -> None:
        HTTP_CLIENT_SUBSCRIBERS.register()
        super().__init__()
