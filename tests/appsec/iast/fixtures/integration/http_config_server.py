import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import sys
from typing import Dict

IAST_ENABLED = {"iast_enabled": True}
IAST_DISABLED = {"iast_enabled": False}
IAST_UNSET = {}  # type: Dict[str, bool]


class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Define the response payload (fixed JSON response)
        response = self.server.canned_response

        # Send response status code
        self.send_response(200)

        # Send headers
        self.send_header("Content-type", "application/json")
        self.end_headers()

        # Send the JSON response
        self.wfile.write(json.dumps(response).encode("utf-8"))


class CustomHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, canned_response):
        super().__init__(server_address, RequestHandlerClass)
        self.canned_response = canned_response


# Start the server
if __name__ == "__main__":
    # Get mode from CLI args
    last_arg = sys.argv[-1]
    if last_arg == "IAST_ENABLED":
        MODE = IAST_ENABLED
    elif last_arg == "IAST_DISABLED":
        MODE = IAST_DISABLED
    elif last_arg == "IAST_UNSET":
        MODE = IAST_UNSET
    else:
        raise ValueError(f"Unknown mode: {last_arg}")

    server_address = ("", 9090)  # Listen on port 9090
    httpd = CustomHTTPServer(server_address, SimpleHandler, canned_response=MODE)
    httpd.serve_forever()
