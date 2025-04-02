import pytest

from tests.appsec.appsec_utils import flask_server
from tests.appsec.appsec_utils import gunicorn_server
from tests.appsec.integrations.flask_tests.utils import _PORT
from tests.appsec.integrations.flask_tests.utils import _request_200


def test_flask_iast_ast_patching_import_error():
    """this is a regression test for a bug with IAST + BeautifulSoup4, we were catching the ImportError exception in
    ddtrace.appsec._iast._loader
    try:
        from . import _html5lib
        register_treebuilders_from(_html5lib)
    except ImportError:
        # They don't have html5lib installed.
        pass
    """
    with flask_server(
        appsec_enabled="false", iast_enabled="true", token=None, port=_PORT, assert_debug=False
    ) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-ast-patching-import-error")

        assert response.status_code == 200
        assert response.content == b"False"


@pytest.mark.parametrize("style", ["re_module", "re_object"])
@pytest.mark.parametrize("endpoint", ["re", "non-re"])
@pytest.mark.parametrize(
    "function",
    [
        "expand",
        "findall",
        "finditer",
        "fullmatch",
        "groups",
        "search",
        "split",
        "string",
        "sub",
        "subn",
    ],
)
def test_flask_iast_ast_patching_re(style, endpoint, function):
    """
    Tests re module patching end to end by checking that re.sub is propagating properly
    """
    filename = "path_traversal_test_file.txt"
    if function in ("groups", "search", "fullmatch", "string", "expand"):
        from urllib.parse import quote_plus

        filename = quote_plus("Isaac Newton")
    with flask_server(
        appsec_enabled="false", iast_enabled="true", token=None, port=_PORT, assert_debug=False
    ) as context:
        _, flask_client, pid = context

        response = flask_client.get(f"/iast-ast-patching-{endpoint}-{function}?style={style}&filename={filename}")

        assert response.status_code == 200
        assert response.content == b"OK"


@pytest.mark.parametrize("style", ["_io_module", "io_module", "io_function", "_io_function"])
@pytest.mark.parametrize(
    "function",
    [
        "bytesio",
        "stringio",
        "bytesio-read",
        "stringio-read",
        "bytesio-untainted",
        "stringio-untainted",
        "bytesio-read-untainted",
        "stringio-read-untainted",
    ],
)
def test_flask_iast_ast_patching_io(style, function, endpoint="io"):
    """
    Tests _io/io BytesIO and StringIO patching end to end
    """
    filename = "path_traversal_test_file.txt"
    with flask_server(
        appsec_enabled="false", iast_enabled="true", token=None, port=_PORT, assert_debug=False
    ) as context:
        _, flask_client, pid = context

        response = flask_client.get(f"/iast-ast-patching-{endpoint}-{function}?style={style}&filename={filename}")

        assert response.status_code == 200
        assert response.content == b"OK"


def test_multiple_requests():
    """we want to validate context is working correctly among multiple request and no race condition creating and
    destroying contexts
    """
    with gunicorn_server(remote_configuration_enabled="false", iast_enabled="true", port=_PORT) as context:
        _, client, pid = context

        _request_200(
            client,
            url="/iast-weak-hash-vulnerability",
            extra_validation=lambda response: b"iast::propagation::error::" not in response.content,
            max_retries=40,
        )
