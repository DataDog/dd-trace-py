import http.server
import socketserver

PORT = 8000

class HTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        print(f"GET request,\nPath: {self.path}\nHeaders:\n{self.headers}")
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"Received GET request")

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        print(f"POST request,\nPath: {self.path}\nHeaders:\n{self.headers}\n\nBody:\n{post_data.decode('utf-8')}")
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"Received POST request")

with socketserver.TCPServer(("", PORT), HTTPRequestHandler) as httpd:
    print(f"Serving at port {PORT}")
    httpd.serve_forever()

