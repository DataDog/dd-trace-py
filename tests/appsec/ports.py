"""Port helpers, kept free of heavy imports.

The shared integrations conftest needs get_free_port for every test under it, including
suites whose venv has none of appsec_utils' dependencies.
"""

import socket


def port_is_available(port: int) -> bool:
    """Whether a server could bind the port right now.

    Binding is the question that matters, since it is what the next server does. Probing with
    connect() instead reports a port as free once a bound server's listen backlog fills, and
    opens real connections to a live server.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", int(port)))
            return True
        except OSError:
            return False


def get_free_port() -> int:
    """A port nothing is bound to right now, for a server that would otherwise reuse a fixed one.

    Tests sharing a fixed port inherit the previous server's orphaned workers, which hold it
    bound well past teardown and make the next bind fail.

    Binds 0.0.0.0 because that is what the servers themselves bind: a port free on loopback can
    already be taken on another interface.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("0.0.0.0", 0))
        return int(sock.getsockname()[1])
