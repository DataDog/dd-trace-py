from tests.appsec.appsec_utils import flask_server
from tests.appsec.appsec_utils import gunicorn_server


def test_flask_when_appsec_reads_empty_body_hang():
    """A bug was detected when running a Flask application locally

    file1.py:
        app.run(debug=True, port=8000)

    then:
       DD_APPSEC_ENABLED=true poetry run ddtrace-run python -m file1:app
       DD_APPSEC_ENABLED=true python -m ddtrace-run python file1.py

    If you make an empty POST request (curl -X POST '127.0.0.1:8000/'), Flask hangs when the ASM handler tries to read
    an empty body
    """
    with flask_server(remote_configuration_enabled="false", token=None) as context:
        _, flask_client, pid = context

        response = flask_client.get("/")

        assert response.status_code == 200
        assert response.content == b"OK_index"

        response = flask_client.post("/test-body-hang")

        assert response.status_code == 200
        assert response.content == b"OK_test-body-hang"


def test_gunicorn_when_appsec_reads_empty_body_no_hang():
    """In relation to the previous test, Gunicorn works correctly, but we added a regression test to ensure that
    no similar bug arises in a production application.
    """
    with gunicorn_server(remote_configuration_enabled="false", token=None) as context:
        _, gunicorn_client, pid = context

        response = gunicorn_client.get("/")

        assert response.status_code == 200
        assert response.content == b"OK_index"

        response = gunicorn_client.post("/test-body-hang")

        assert response.status_code == 200
        assert response.content == b"OK_test-body-hang"
