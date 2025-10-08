import httpx
from ddtrace import patch

patch(httpx=True)

def sync_example():
    """Synchronous requests"""
    # Simple GET request - automatically traced
    response = httpx.get("https://example.com/", timeout=10.0)
    print(f"Sync GET: {response.status_code}")


def sync_post_example():
    """Synchronous POST request"""
    response = httpx.post("https://httpbin.org/post", json={"key": "value"}, timeout=10.0)
    print(f"Sync POST: {response.status_code}")


async def async_example():
    """Asynchronous requests"""
    async with httpx.AsyncClient() as client:
        response = await client.get("https://httpbin.org/json")
        print(f"Async GET: {response.status_code}")


if __name__ == "__main__":
    sync_example()
