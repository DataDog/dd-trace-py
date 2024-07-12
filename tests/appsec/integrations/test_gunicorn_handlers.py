import os

import pytest
from requests.exceptions import ConnectionError

from tests.appsec.appsec_utils import flask_server
from tests.appsec.appsec_utils import gunicorn_server


_PORT = 8030


@pytest.mark.parametrize("appsec_enabled", ("true", "false"))
@pytest.mark.parametrize("appsec_standalone_enabled", ("true", "false"))
@pytest.mark.parametrize("tracer_enabled", ("true", "false"))
@pytest.mark.parametrize("server", ((gunicorn_server, flask_server)))
def test_when_appsec_reads_chunked_requests(appsec_enabled, appsec_standalone_enabled, tracer_enabled, server):
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
        with server(
            appsec_enabled=appsec_enabled,
            tracer_enabled=tracer_enabled,
            appsec_standalone_enabled=appsec_standalone_enabled,
            remote_configuration_enabled="false",
            token=None,
            port=_PORT,
        ) as context:
            _, gunicorn_client, pid = context
            headers = {
                # requests won't add a boundary if this header is set when you pass files=
                # 'Content-Type': 'multipart/form-data',
                "Transfer-Encoding": "chunked",
            }
            response = gunicorn_client.post("/submit/file", data=read_in_chunks(filepath), headers=headers)

            assert response.status_code == 200
            assert response.content == b"OK_file"
    finally:
        os.remove(filepath)


@pytest.mark.skip(reason="We're still finding a solution to this corner case. It hangs in CI")
@pytest.mark.parametrize("appsec_enabled", ("true", "false"))
@pytest.mark.parametrize("appsec_standalone_enabled", ("true", "false"))
@pytest.mark.parametrize("tracer_enabled", ("true", "false"))
@pytest.mark.parametrize("server", ((gunicorn_server, flask_server)))
def test_corner_case_when_appsec_reads_chunked_request_with_no_body(
    appsec_enabled, appsec_standalone_enabled, tracer_enabled, server
):
    """if Gunicorn receives an empty body but Transfer-Encoding is "chunked", the application hangs but gunicorn
    control it with a timeout
    """
    if not (appsec_enabled == "true" and tracer_enabled == "false"):
        with server(
            appsec_enabled=appsec_enabled,
            tracer_enabled=tracer_enabled,
            appsec_standalone_enabled=appsec_standalone_enabled,
            remote_configuration_enabled="false",
            token=None,
            port=_PORT,
        ) as context:
            _, gunicorn_client, pid = context
            headers = {
                "Transfer-Encoding": "chunked",
            }
            with pytest.raises(ConnectionError):
                _ = gunicorn_client.post("/submit/file", headers=headers)


@pytest.mark.parametrize("appsec_enabled", ("true", "false"))
@pytest.mark.parametrize("appsec_standalone_enabled", ("true", "false"))
@pytest.mark.parametrize("tracer_enabled", ("true", "false"))
@pytest.mark.parametrize("server", ((gunicorn_server, flask_server)))
def test_when_appsec_reads_empty_body_no_hang(appsec_enabled, appsec_standalone_enabled, tracer_enabled, server):
    """A bug was detected when running a Flask application locally

    file1.py:
        app.run(debug=True, port=8000)

    then:
       DD_APPSEC_ENABLED=true poetry run ddtrace-run python -m file1:app
       DD_APPSEC_ENABLED=true python -m ddtrace-run python file1.py

    If you make an empty POST request (curl -X POST '127.0.0.1:8000/'), Flask hangs when the ASM handler tries to read
    an empty body
    """
    with server(
        appsec_enabled=appsec_enabled,
        appsec_standalone_enabled=appsec_standalone_enabled,
        tracer_enabled=tracer_enabled,
        remote_configuration_enabled="false",
        token=None,
        port=_PORT,
    ) as context:
        _, gunicorn_client, pid = context

        response = gunicorn_client.get("/")

        assert response.status_code == 200
        assert response.content == b"OK_index"

        response = gunicorn_client.post("/test-body-hang")

        assert response.status_code == 200
        assert response.content == b"OK_test-body-hang"


@pytest.mark.skip(reason="We're still finding a solution to this corner case. It hangs in CI")
@pytest.mark.parametrize("appsec_enabled", ("true", "false"))
@pytest.mark.parametrize("appsec_standalone_enabled", ("true", "false"))
@pytest.mark.parametrize("tracer_enabled", ("true", "false"))
@pytest.mark.parametrize("server", ((gunicorn_server,)))
def test_when_appsec_reads_empty_body_and_content_length_no_hang(
    appsec_enabled, appsec_standalone_enabled, tracer_enabled, server
):
    """We test Gunicorn, Flask server hangs forever in all cases"""
    with server(
        appsec_enabled=appsec_enabled,
        appsec_standalone_enabled=appsec_standalone_enabled,
        tracer_enabled=tracer_enabled,
        remote_configuration_enabled="false",
        token=None,
        port=_PORT,
    ) as context:
        _, gunicorn_client, pid = context

        headers = {
            "Content-Length": "1000",
        }

        response = gunicorn_client.post("/test-body-hang", headers=headers)
        assert response.status_code == 200
        assert response.content == b"OK_test-body-hang"
