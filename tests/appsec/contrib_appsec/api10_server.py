from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import os
import socket
import sys
import threading


API10_RESPONSE_BODY = b'{"payload": "api10-response-body"}'

ROUTES = {
    "/request-headers": (200, b"ok", {"Content-Type": "text/plain"}),
    "/request-body": (200, b"ok", {"Content-Type": "text/plain"}),
    "/response-headers": (200, b"ok", {"Content-Type": "text/plain", "x-api10-response": "api10-response-header"}),
    "/response-body": (200, API10_RESPONSE_BODY, {"Content-Type": "application/json"}),
    "/response-status": (210, b"ok", {"Content-Type": "application/json"}),
    "/redirect-source": (
        302,
        API10_RESPONSE_BODY,
        {"Content-Type": "application/json", "Location": "/redirect-target", "x-api10-redirect": "api10-redirect"},
    ),
    "/redirect-target": (200, API10_RESPONSE_BODY, {"Content-Type": "application/json"}),
}
NOT_FOUND = (404, b"not found", {"Content-Type": "text/plain"})


class Api10Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._respond()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length:
            self.rfile.read(content_length)
        self._respond()

    def log_message(self, *args, **kwargs):
        pass

    def _respond(self):
        status, body, headers = ROUTES.get(self.path, NOT_FOUND)
        self.send_response(status)
        for header, value in headers.items():
            self.send_header(header, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def exit_with_parent():
    sys.stdin.buffer.read()
    os._exit(0)


def main():
    listener = socket.socket(fileno=int(sys.argv[1]))
    server = ThreadingHTTPServer(listener.getsockname(), Api10Handler, bind_and_activate=False)
    server.socket.close()
    server.socket = listener
    threading.Thread(target=exit_with_parent, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
