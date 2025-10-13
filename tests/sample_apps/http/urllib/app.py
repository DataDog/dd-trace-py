import urllib.request
from ddtrace import patch

patch(urllib=True)

def send_request(method: str, url: str):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=10.0) as response:
        print(f"{method} {url}: {response.status}")