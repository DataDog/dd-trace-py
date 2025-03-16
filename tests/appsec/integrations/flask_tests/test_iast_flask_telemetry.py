from ddtrace.appsec._iast._handlers import _on_flask_patch
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from tests.appsec.appsec_utils import flask_server
from tests.utils import flaky
from tests.utils import override_global_config


def test_iast_span_metrics():
    # TODO: move tests/telemetry/conftest.py::test_agent_session into a common conftest
    with flask_server(iast_enabled="true", token=None, port=8050) as context:
        _, flask_client, pid = context

        response = flask_client.get("/iast-cmdi-vulnerability?filename=path_traversal_test_file.txt")

        assert response.status_code == 200
        assert response.content == b"OK"


@flaky(1735812000)
def test_flask_instrumented_metrics(telemetry_writer):
    with override_global_config(dict(_iast_enabled=True)):
        _on_flask_patch((2, 0, 0))

    metrics_result = telemetry_writer._namespace._metrics_data
    assert metrics_result["generate-metrics"]["iast"]

    metrics_source_tags_result = [
        metric._tags[0][1]
        for metric in metrics_result["generate-metrics"]["iast"].values()
        if metric.name == "instrumented.source"
    ]

    assert origin_to_str(OriginType.HEADER_NAME) in metrics_source_tags_result
    assert origin_to_str(OriginType.HEADER) in metrics_source_tags_result
    assert origin_to_str(OriginType.PARAMETER) in metrics_source_tags_result
    assert origin_to_str(OriginType.PATH) in metrics_source_tags_result
    assert origin_to_str(OriginType.PATH_PARAMETER) in metrics_source_tags_result
    assert origin_to_str(OriginType.QUERY) in metrics_source_tags_result
    assert origin_to_str(OriginType.BODY) in metrics_source_tags_result
    assert origin_to_str(OriginType.COOKIE) in metrics_source_tags_result
    assert origin_to_str(OriginType.COOKIE_NAME) in metrics_source_tags_result
    assert origin_to_str(OriginType.PARAMETER_NAME) in metrics_source_tags_result


def test_flask_instrumented_metrics_iast_disabled(telemetry_writer):
    with override_global_config(dict(_iast_enabled=False)):
        _on_flask_patch((2, 0, 0))

    metrics_result = telemetry_writer._namespace._metrics_data
    metrics_source_tags_result = [metric._tags for metric in metrics_result["generate-metrics"]["iast"].values()]

    assert len(metrics_source_tags_result) == 0
