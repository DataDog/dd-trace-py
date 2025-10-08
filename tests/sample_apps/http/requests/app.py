import requests


def sync_example():
    """Synchronous requests"""
    # Simple GET request - automatically traced
    response = requests.get("https://example.com/", timeout=10.0)
    print(f"Sync GET: {response.status_code}")


def sync_post_example():
    """Synchronous POST request"""
    response = requests.post("https://httpbin.org/post", json={"key": "value"}, timeout=10.0)
    print(f"Sync POST: {response.status_code}")


if __name__ == "__main__":
    sync_example()
