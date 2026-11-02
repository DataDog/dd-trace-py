import pytest


@pytest.mark.subprocess(
    parametrize={"DD_REMOTE_CONFIGURATION_ENABLED": ["true", "false"]},
    env={"DD_CODE_ORIGIN_FOR_SPANS_ENABLED": "true", "DD_LLMOBS_ENABLED": None},
    err=None,
)
def test_requests_do_not_initialize_disabled_products():
    import sys

    from flask import Flask

    from ddtrace import tracer
    import ddtrace.auto  # noqa: F401

    app = Flask(__name__)

    @app.route("/")
    def index():
        assert tracer.current_root_span().get_tag("_dd.code_origin.type") == "entry"
        return "ok"

    with app.test_client() as client:
        for _ in range(3):
            response = client.get("/")
            assert response.status_code == 200
            assert response.data == b"ok"

    for module in (
        "ddtrace.llmobs._llmobs",
        "ddtrace.llmobs._experiment",
        "ddtrace.debugging._debugger",
        "ddtrace.debugging._origin._span_snapshot",
        "ddtrace.debugging._uploader",
        "ddtrace.internal.symbol_db.symbols",
    ):
        assert module not in sys.modules
