from unittest.mock import patch

import pytest

from ddtrace.internal import process_tags
from ddtrace.internal.process_tags import _process_tag_reload
from ddtrace.internal.process_tags import normalize_tag
from ddtrace.settings._config import config
from tests.subprocesstest import run_in_subprocess
from tests.utils import TracerTestCase


@pytest.mark.parametrize(
    "input_tag,expected",
    [
        ("HelloWorld", "helloworld"),
        ("Hello@World!", "hello_world_"),
        ("HeLLo123", "hello123"),
        ("hello world", "hello_world"),
        ("a/b.c_d-e", "a/b.c_d-e"),
        ("héllø", "h_ll_"),
        ("", ""),
        ("💡⚡️", "___"),
        ("!foo@", "_foo_"),
        ("123_abc.DEF-ghi/jkl", "123_abc.def-ghi/jkl"),
        ("Env:Prod-Server#1", "env_prod-server_1"),
    ],
)
def test_normalize_tag(input_tag, expected):
    assert normalize_tag(input_tag) == expected


class TestProcessTags(TracerTestCase):
    def setUp(self):
        super(TestProcessTags, self).setUp()
        self._original_process_tags_enabled = config._process_tags_enabled
        self._original_process_tags = process_tags.process_tags

    def tearDown(self):
        config._process_tags_enabled = self._original_process_tags_enabled
        process_tags.process_tags = self._original_process_tags
        super().tearDown()

    @pytest.mark.snapshot
    def test_process_tags_deactivated(self):
        config._process_tags_enabled = False
        _process_tag_reload()

        with self.tracer.trace("test"):
            pass

    @pytest.mark.snapshot
    def test_process_tags_activated(self):
        with patch("sys.argv", ["/path/to/test_script.py"]), patch("os.getcwd", return_value="/path/to/workdir"):
            config._process_tags_enabled = True
            _process_tag_reload()

            with self.tracer.trace("parent"):
                with self.tracer.trace("child"):
                    pass

    @pytest.mark.snapshot
    def test_process_tags_edge_case(self):
        with patch("sys.argv", ["/test_script"]), patch("os.getcwd", return_value="/path/to/workdir"):
            config._process_tags_enabled = True
            _process_tag_reload()

            with self.tracer.trace("span"):
                pass

    @pytest.mark.snapshot
    def test_process_tags_error(self):
        with patch("sys.argv", []), patch("os.getcwd", return_value="/path/to/workdir"):
            config._process_tags_enabled = True

            with self.override_global_config(dict(_telemetry_enabled=False)):
                with patch("ddtrace.internal.process_tags.log") as mock_log:
                    _process_tag_reload()

                    with self.tracer.trace("span"):
                        pass

                    # Check if debug log was called
                    mock_log.debug.assert_called_once()
                    call_args = mock_log.debug.call_args[0]
                    assert "failed to get process_tags" in call_args[0]

    @pytest.mark.snapshot
    @run_in_subprocess(env_overrides=dict(DD_TRACE_PARTIAL_FLUSH_ENABLED="true", DD_TRACE_PARTIAL_FLUSH_MIN_SPANS="2"))
    def test_process_tags_partial_flush(self):
        with patch("sys.argv", ["/path/to/test_script.py"]), patch("os.getcwd", return_value="/path/to/workdir"):
            config._process_tags_enabled = True
            _process_tag_reload()

            with self.override_global_config(dict(_partial_flush_enabled=True, _partial_flush_min_spans=2)):
                with self.tracer.trace("parent"):
                    with self.tracer.trace("child1"):
                        pass
                    with self.tracer.trace("child2"):
                        pass
