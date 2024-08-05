from tests.appsec.appsec_utils import flask_server


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
    with flask_server(appsec_enabled="false", iast_enabled="true", token=None, port=8020, assert_debug=True) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-ast-patching-import-error")

        assert response.status_code == 200
        assert response.content == b"False"
