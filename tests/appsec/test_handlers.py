import os

import pytest

from tests.appsec.appsec_utils import flask_server
from tests.appsec.appsec_utils import gunicorn_server


@pytest.mark.parametrize("appsec_enabled", (("true", "false")))
def test_flask_when_appsec_reads_empty_body_hang(appsec_enabled):
    """A bug was detected when running a Flask application locally

    file1.py:
        app.run(debug=True, port=8000)

    then:
       DD_APPSEC_ENABLED=true poetry run ddtrace-run python -m file1:app
       DD_APPSEC_ENABLED=true python -m ddtrace-run python file1.py

    If you make an empty POST request (curl -X POST '127.0.0.1:8000/'), Flask hangs when the ASM handler tries to read
    an empty body
    """
    with flask_server(appsec_enabled=appsec_enabled, remote_configuration_enabled="false", token=None) as context:
        _, flask_client, pid = context

        response = flask_client.get("/")

        assert response.status_code == 200
        assert response.content == b"OK_index"

        response = flask_client.post("/test-body-hang")

        assert response.status_code == 200
        assert response.content == b"OK_test-body-hang"


@pytest.mark.parametrize("appsec_enabled", (("true", "false")))
def test_gunicorn_when_appsec_reads_chunked_requests(appsec_enabled):
    def read_in_chunks(filepath, chunk_size=1024):
        file_object = open(filepath, "rb")
        while True:
            data = file_object.read(chunk_size)
            if not data:
                break
            yield data

    filepath = "test_gunicorn_when_appsec_reads_chunked_requests.txt"
    with open(filepath, "w") as f:
        for i in range(1024):
            f.writelines("1234567890_qwertyuiopasdfghjklzxcvbnm_{}".format(i))

    try:
        with gunicorn_server(
            appsec_enabled=appsec_enabled, remote_configuration_enabled="false", token=None
        ) as context:
            _, gunicorn_client, pid = context
            headers = {}
            response = gunicorn_client.post("/submit/file", data=read_in_chunks(filepath), headers=headers)

            assert response.status_code == 200
            assert response.content == b"OK_file"
    finally:
        os.remove(filepath)


@pytest.mark.parametrize("appsec_enabled", (("true", "false")))
def test_gunicorn_when_appsec_reads_empty_body_no_hang(appsec_enabled):
    """In relation to the previous test, Gunicorn works correctly, but we added a regression test to ensure that
    no similar bug arises in a production application.
    """
    with gunicorn_server(appsec_enabled=appsec_enabled, remote_configuration_enabled="false", token=None) as context:
        _, gunicorn_client, pid = context

        response = gunicorn_client.get("/")

        assert response.status_code == 200
        assert response.content == b"OK_index"

        response = gunicorn_client.post("/test-body-hang")

        assert response.status_code == 200
        assert response.content == b"OK_test-body-hang"
