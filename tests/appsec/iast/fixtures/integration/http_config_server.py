from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import sys


IAST_ENABLED = {"iast_enabled": True}
IAST_DISABLED = {"iast_enabled": False}
IAST_UNSET = {}


class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Define the response payload (fixed JSON response)
        path = self.path[:-1] if self.path.endswith("/") else self.path
        if path.endswith("IAST_ENABLED"):
            response = IAST_ENABLED
        elif path.endswith("IAST_DISABLED"):
            response = IAST_DISABLED
        else:
            response = IAST_UNSET

        # Send response status code
        self.send_response(200)

        # Send headers
        self.send_header("Content-type", "application/json")
        self.end_headers()

        # Send the JSON response
        self.wfile.write(json.dumps(response).encode("utf-8"))


# Start the server
if __name__ == "__main__":
    try:
        SERVER_PORT = int(sys.argv[-1])
    except Exception:
        SERVER_PORT = 9090
    server_address = ("", SERVER_PORT)
    httpd = HTTPServer(server_address, SimpleHandler)
    httpd.serve_forever()
