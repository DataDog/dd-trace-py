import unittest.mock

import pytest

from ddtrace.settings.asm import config as asm_config
from tests.utils import TracerSpanContainer
from tests.utils import _build_tree


@pytest.fixture
def test_spans(interface, check_waf_timeout):
    container = TracerSpanContainer(interface.tracer)
    assert check_waf_timeout is None
    yield container
    container.reset()


@pytest.fixture
def root_span(test_spans):
    # get the first root span
    def get_root_span():
        for span in test_spans.spans:
            if span.parent_id is None:
                return _build_tree(test_spans.spans, span)

    yield get_root_span


@pytest.fixture
def check_waf_timeout(request, printer):
    with unittest.mock.patch("ddtrace.appsec._processor._set_waf_error_metric", autospec=True) as mock_metrics:
        # change timeout to 5 seconds to avoid flaky timeouts
        previous_timeout = asm_config._waf_timeout
        asm_config._waf_timeout = 5000.0
        test_failed = request.session.testsfailed
        yield
        if request.session.testsfailed > test_failed:
            for args in mock_metrics.call_args_list:
                args = list(args)
                if args[0][0] == "WAF run. Timeout errors":
                    # report the waf timeout error as an addtionnal test error
                    pytest.fail(f"WAF timeout detected. WAF info {args[0][2]}")
        asm_config._waf_timeout = previous_timeout


@pytest.fixture
def get_tag(root_span):
    yield lambda name: root_span().get_tag(name)


def no_op(msg: str) -> None:  # noqa: ARG001
    """Do nothing."""


@pytest.fixture(name="printer")
def printer(request):
    terminal_reporter = request.config.pluginmanager.getplugin("terminalreporter")
    capture_manager = request.config.pluginmanager.get_plugin("capturemanager")

    def printer(*args, **kwargs):
        with capture_manager.global_and_fixture_disabled():
            if terminal_reporter is not None:  # pragma: no branch
                terminal_reporter.write_line(*args, **kwargs)

    return printer
