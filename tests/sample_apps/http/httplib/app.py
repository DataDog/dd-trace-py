import http.client
from urllib.parse import urlparse
from ddtrace import patch

patch(httplib=True)

def send_request(method: str, url: str):
    parsed_url = urlparse(url)
    if parsed_url.scheme == "https":
        conn = http.client.HTTPSConnection(parsed_url.netloc, timeout=10.0)
    else:
        conn = http.client.HTTPConnection(parsed_url.netloc, timeout=10.0)

    path = parsed_url.path or "/"
    if parsed_url.query:
        path += f"?{parsed_url.query}"

    conn.request(method, path)
    response = conn.getresponse()
    print(f"{method} {url}: {response.status}")
    conn.close()