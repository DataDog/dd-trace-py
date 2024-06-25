import pytest


@pytest.fixture(name="printer")
def printer(request):
    terminal_reporter = request.config.pluginmanager.getplugin("terminalreporter")
    capture_manager = request.config.pluginmanager.get_plugin("capturemanager")

    def printer(*args, **kwargs):
        with capture_manager.global_and_fixture_disabled():
            if terminal_reporter is not None:  # pragma: no branch
                terminal_reporter.write_line(*args, **kwargs)

    return printer
