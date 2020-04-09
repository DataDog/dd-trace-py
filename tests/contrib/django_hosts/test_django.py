def test_django_hosts_request(client, test_spans):
    """
    When using django_hosts
        We properly set the resource name for the request
    """
    resp = client.get("/", HTTP_HOST="app.example.org")
    assert resp.status_code == 200
    assert resp.content == b"Hello, test app."

    spans = test_spans.get_spans()
    # Assert the correct number of traces and spans
    assert len(spans) == 26

    # Assert the structure of the root `django.request` span
    root = test_spans.get_root_span()

    meta = {
        "django.request.class": "django.core.handlers.wsgi.WSGIRequest",
        "django.response.class": "django.http.response.HttpResponse",
        "django.user.is_authenticated": "False",
        "django.view": "tests.contrib.django_hosts.views.index",
        "http.method": "GET",
        "http.status_code": "200",
        "http.url": "http://app.example.org/",
        "http.route": "^$",
    }

    root.assert_matches(
        name="django.request",
        service="django",
        resource="GET ^$",
        parent_id=None,
        span_type="http",
        error=0,
        meta=meta,
    )
