import django
import pytest

from tests import snapshot


@pytest.mark.skipif(not (django.VERSION > (2, 0) and django.VERSION < (2, 2)), reason="")
@snapshot()
def test_urlpatterns_include_21x(client):
    """
    When a view is specified using `django.urls.include`
        The view is traced
    """
    assert client.get("/include/test/").status_code == 200


@pytest.mark.skipif(django.VERSION < (2, 2), reason="")
@snapshot()
def test_urlpatterns_include(client):
    """
    When a view is specified using `django.urls.include`
        The view is traced
    """
    assert client.get("/include/test/").status_code == 200


@pytest.mark.skipif(django.VERSION >= (1, 9), reason="")
@snapshot()
def test_middleware_trace_callable_view_18x(client):
    # ensures that the internals are properly traced when using callable views
    assert client.get("/feed-view/").status_code == 200


@pytest.mark.skipif(not (django.VERSION >= (1, 9) and django.VERSION < (1, 12)), reason="")
@snapshot()
def test_middleware_trace_callable_view_111x(client):
    # ensures that the internals are properly traced when using callable views
    assert client.get("/feed-view/").status_code == 200


@pytest.mark.skipif(not (django.VERSION > (1, 12) and django.VERSION < (2, 2)), reason="")
@snapshot()
def test_middleware_trace_callable_view_21x(client):
    # ensures that the internals are properly traced when using callable views
    assert client.get("/feed-view/").status_code == 200


@pytest.mark.skipif(django.VERSION < (2, 2), reason="")
@snapshot()
def test_middleware_trace_callable_view(client):
    # ensures that the internals are properly traced when using callable views
    assert client.get("/feed-view/").status_code == 200


@pytest.mark.skipif(django.VERSION >= (1, 9), reason="")
@snapshot()
def test_middleware_trace_partial_based_view_18x(client):
    # ensures that the internals are properly traced when using a function views
    assert client.get("/partial-view/").status_code == 200


@pytest.mark.skipif(not (django.VERSION >= (1, 9) and django.VERSION < (1, 12)), reason="")
@snapshot()
def test_middleware_trace_partial_based_view_111x(client):
    # ensures that the internals are properly traced when using a function views
    assert client.get("/partial-view/").status_code == 200


@pytest.mark.skipif(not (django.VERSION > (1, 12) and django.VERSION < (2, 2)), reason="")
@snapshot()
def test_middleware_trace_partial_based_view_21x(client):
    # ensures that the internals are properly traced when using a function views
    assert client.get("/partial-view/").status_code == 200


@pytest.mark.skipif(django.VERSION < (2, 2), reason="")
@snapshot()
def test_middleware_trace_partial_based_view(client):
    # ensures that the internals are properly traced when using a function views
    assert client.get("/partial-view/").status_code == 200


@pytest.mark.skipif(django.VERSION >= (1, 9), reason="")
@pytest.mark.django_db
@snapshot()
def test_safe_string_encoding_18x(client):
    assert client.get("/safe-template/").status_code == 200


@pytest.mark.skipif(not (django.VERSION >= (1, 9) and django.VERSION < (1, 12)), reason="")
@pytest.mark.django_db
@snapshot()
def test_safe_string_encoding_111x(client):
    assert client.get("/safe-template/").status_code == 200


@pytest.mark.skipif(not (django.VERSION > (1, 12) and django.VERSION < (2, 2)), reason="")
@pytest.mark.django_db
@snapshot()
def test_safe_string_encoding_21x(client):
    assert client.get("/safe-template/").status_code == 200


@pytest.mark.skipif(django.VERSION < (2, 2), reason="")
@pytest.mark.django_db
@snapshot()
def test_safe_string_encoding(client):
    assert client.get("/safe-template/").status_code == 200
