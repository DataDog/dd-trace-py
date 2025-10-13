import httpx
from ddtrace import patch

patch(httpx=True)

def send_request(method: str, url: str):
    response = httpx.request(method=method, url=url, timeout=10.0)
    print(f"{method} {url}: {response.status_code}")

def get_request(url: str):
    response = httpx.get(url=url, timeout=10.0)
    print(f"GET {url}: {response.status_code}")