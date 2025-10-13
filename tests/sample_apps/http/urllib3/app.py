import urllib3
from ddtrace import patch

patch(urllib3=True)

def send_request(method: str, url: str):
    http = urllib3.PoolManager()
    response = http.request(method=method, url=url, timeout=10.0)
    print(f"{method} {url}: {response.status}")