import requests
from ddtrace import patch

patch(requests=True)

def send_request(method: str, url: str):
    response = requests.request(method=method, url=url, timeout=10.0)
    print(f"{method} {url}: {response.status_code}")

def get_request(url: str):
    response = requests.get(url=url, timeout=10.0)
    print(f"GET {url}: {response.status_code}")
