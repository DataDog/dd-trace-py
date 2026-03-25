import pytest


@pytest.mark.snapshot()
def test_django_hosts_request(client):
    """
    When using django_hosts
        We properly set the resource name for the request
    """
    resp = client.get("/", HTTP_HOST="app.example.org")
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."
