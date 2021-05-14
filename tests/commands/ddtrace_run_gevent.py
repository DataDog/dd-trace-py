import socket

import gevent.socket


# https://stackoverflow.com/a/24770674
if __name__ == "__main__":
    assert socket.socket is gevent.socket.socket
    print("Test success")
