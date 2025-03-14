import ddtrace.auto  # noqa: F401


# ensure the tracer is loaded and started first for possible iast patching
print(f"ddtrace version {ddtrace.version.get_version()}")


import pytest  # noqa: E402

from ddtrace.settings.asm import config as asm_config  # noqa: E402
from tests.utils import TracerSpanContainer  # noqa: E402
from tests.utils import _build_tree  # noqa: E402


@pytest.fixture(scope="function", autouse=True)
def _dj_autoclear_mailbox() -> None:
    # Override the `_dj_autoclear_mailbox` test fixture in `pytest_django`.
    pass


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
        # In case root span is not found, try to find a span with a local root
        for span in test_spans.spans:
            if span._local_root is not None:
                return _build_tree(test_spans.spans, span._local_root)

    yield get_root_span


@pytest.fixture
def check_waf_timeout(request):
    # change timeout to 50 seconds to avoid flaky timeouts
    previous_timeout = asm_config._waf_timeout
    asm_config._waf_timeout = 50_000.0
    yield
    asm_config._waf_timeout = previous_timeout


@pytest.fixture
def get_tag(test_spans, root_span):
    # checking both root spans and web spans for the tag
    def get(name):
        for span in test_spans.spans:
            if span.parent_id is None or span.span_type == "web":
                res = span.get_tag(name)
                if res is not None:
                    return res
        return root_span().get_tag(name)

    yield get


@pytest.fixture
def get_metric(root_span):
    yield lambda name: root_span().get_metric(name)


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
