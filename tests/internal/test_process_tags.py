from unittest.mock import patch

import pytest

from ddtrace.internal import process_tags
from ddtrace.internal.constants import PROCESS_TAGS
from ddtrace.internal.process_tags import ENTRYPOINT_BASEDIR_TAG
from ddtrace.internal.process_tags import ENTRYPOINT_NAME_TAG
from ddtrace.internal.process_tags import ENTRYPOINT_TYPE_TAG
from ddtrace.internal.process_tags import ENTRYPOINT_WORKDIR_TAG
from ddtrace.internal.process_tags import _compute_process_tag
from ddtrace.internal.process_tags import normalize_tag_value
from tests.subprocesstest import run_in_subprocess
from tests.utils import TracerTestCase
from tests.utils import process_tag_reload


TEST_SCRIPT_PATH = "/path/to/test_script.py"
TEST_WORKDIR_PATH = "/path/to/workdir"


@pytest.mark.parametrize(
    "input_tag,expected",
    [
        ("#test_starting_hash", "test_starting_hash"),
        ("TestCAPSandSuch", "testcapsandsuch"),
        ("Test Conversion Of Weird !@#$%^&**() Characters", "test_conversion_of_weird_characters"),
        ("$#weird_starting", "weird_starting"),
        ("allowed:c0l0ns", "allowed_c0l0ns"),
        ("1love", "1love"),
        ("/love2", "/love2"),
        ("ünicöde", "ünicöde"),
        ("ünicöde:metäl", "ünicöde_metäl"),
        ("Data🐨dog🐶 繋がっ⛰てて", "data_dog_繋がっ_てて"),
        (" spaces   ", "spaces"),
        (" #hashtag!@#spaces #__<>#  ", "hashtag_spaces"),
        (":testing", "testing"),
        ("_foo", "foo"),
        (":::test", "test"),
        ("contiguous_____underscores", "contiguous_underscores"),
        ("foo_", "foo"),
        ("\u017fodd_\u017fcase\u017f", "\u017fodd_\u017fcase\u017f"),
        ("", ""),
        (" ", ""),
        ("ok", "ok"),
        ("™Ö™Ö™™Ö™", "ö_ö_ö"),
        ("AlsO:ök", "also_ök"),
        (":still_ok", "still_ok"),
        ("___trim", "trim"),
        ("12.:trim@", "12._trim"),
        ("12.:trim@@", "12._trim"),
        ("fun:ky__tag/1", "fun_ky_tag/1"),
        ("fun:ky@tag/2", "fun_ky_tag/2"),
        ("fun:ky@@@tag/3", "fun_ky_tag/3"),
        ("tag:1/2.3", "tag_1/2.3"),
        ("---fun:k####y_ta@#g/1_@@#", "---fun_k_y_ta_g/1"),
        ("AlsO:œ#@ö))œk", "also_œ_ö_œk"),
        ("test\x99\x8faaa", "test_aaa"),
        ("test\x99\x8f", "test"),
        ("a" * 888, "a" * 100),
        ("a" + "🐶" * 799 + "b", "a"),
        ("a" + "\ufffd", "a"),
        ("a" + "\ufffd" + "\ufffd", "a"),
        ("a" + "\ufffd" + "\ufffd" + "b", "a_b"),
        (
            "A0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
            " 00000000000",
            "a0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000_0",
        ),
    ],
)
def test_normalize_tag(input_tag, expected):
    assert normalize_tag_value(input_tag) == expected


@pytest.mark.parametrize(
    "excluded_value",
    ["/", "\\", "bin", "", None],
)
def test_compute_process_tag_excluded_values(excluded_value):
    result = _compute_process_tag("test_key", lambda: excluded_value)
    assert result is None


class TestProcessTags(TracerTestCase):
    def setUp(self):
        super(TestProcessTags, self).setUp()
        self._original_process_tags = process_tags.process_tags
        self._original_process_tags_list = process_tags.process_tags_list

    def tearDown(self):
        process_tags.process_tags = self._original_process_tags
        process_tags.process_tags_list = self._original_process_tags_list
        super().tearDown()

    @run_in_subprocess(env_overrides=dict(DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED="False"))
    def test_process_tags_deactivated(self):
        with self.tracer.trace("test"):
            pass

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert PROCESS_TAGS not in span._meta

    @run_in_subprocess
    def test_process_tags_enabled_by_default(self):
        with self.tracer.trace("test"):
            pass

        span = self.get_spans()[0]

        assert span is not None
        assert PROCESS_TAGS in span._meta

    def test_process_tags_activated(self):
        with patch("sys.argv", [TEST_SCRIPT_PATH]), patch("os.getcwd", return_value=TEST_WORKDIR_PATH):
            process_tag_reload()

            with self.tracer.trace("parent"):
                with self.tracer.trace("child"):
                    pass

        spans = self.get_spans()
        assert len(spans) == 2

        parent_span = spans[0]
        child_span = spans[1]

        assert PROCESS_TAGS in parent_span._meta
        process_tags_value = parent_span._meta[PROCESS_TAGS]
        assert f"{ENTRYPOINT_BASEDIR_TAG}:to" in process_tags_value
        assert f"{ENTRYPOINT_NAME_TAG}:test_script" in process_tags_value
        assert f"{ENTRYPOINT_TYPE_TAG}:script" in process_tags_value
        assert f"{ENTRYPOINT_WORKDIR_TAG}:workdir" in process_tags_value

        assert PROCESS_TAGS not in child_span._meta

    def test_process_tags_edge_case(self):
        with patch("sys.argv", ["/test_script"]), patch("os.getcwd", return_value=TEST_WORKDIR_PATH):
            process_tag_reload()

            with self.tracer.trace("span"):
                pass

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        # When script starts with '/', entrypoint.basedir should be excluded
        assert PROCESS_TAGS in span._meta
        process_tags_value = span._meta[PROCESS_TAGS]
        assert ENTRYPOINT_BASEDIR_TAG not in process_tags_value
        assert f"{ENTRYPOINT_NAME_TAG}:test_script" in process_tags_value
        assert f"{ENTRYPOINT_TYPE_TAG}:script" in process_tags_value
        assert f"{ENTRYPOINT_WORKDIR_TAG}:workdir" in process_tags_value

    def test_process_tags_error(self):
        with patch("sys.argv", []), patch("os.getcwd", return_value=TEST_WORKDIR_PATH):
            with self.override_global_config(dict(_telemetry_enabled=False)):
                with patch("ddtrace.internal.process_tags.log") as mock_log:
                    process_tag_reload()

                    with self.tracer.trace("span"):
                        pass

                    # Check if debug log was called
                    assert mock_log.debug.call_count == 2
                    call_args1 = mock_log.debug.call_args_list[0][0]
                    call_args2 = mock_log.debug.call_args_list[1][0]

                    assert "failed to get process tag" in call_args1[0], (
                        f"Expected error message not found. Got: {call_args1[0]}"
                    )
                    assert call_args1[1] == ENTRYPOINT_BASEDIR_TAG, f"Expected tag key not found. Got: {call_args1[1]}"

                    assert "failed to get process tag" in call_args2[0], (
                        f"Expected error message not found. Got: {call_args2[0]}"
                    )
                    assert call_args2[1] == ENTRYPOINT_NAME_TAG, f"Expected tag key not found. Got: {call_args2[1]}"

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        assert PROCESS_TAGS in span._meta
        process_tags_value = span._meta[PROCESS_TAGS]

        # basedir and name should be missing due to errors
        assert ENTRYPOINT_BASEDIR_TAG not in process_tags_value
        assert ENTRYPOINT_NAME_TAG not in process_tags_value

        # type and workdir should be present
        assert f"{ENTRYPOINT_TYPE_TAG}:script" in process_tags_value
        assert f"{ENTRYPOINT_WORKDIR_TAG}:workdir" in process_tags_value

    @run_in_subprocess(env_overrides=dict(DD_TRACE_PARTIAL_FLUSH_ENABLED="true", DD_TRACE_PARTIAL_FLUSH_MIN_SPANS="2"))
    def test_process_tags_partial_flush(self):
        with patch("sys.argv", [TEST_SCRIPT_PATH]), patch("os.getcwd", return_value=TEST_WORKDIR_PATH):
            process_tag_reload()

            with self.tracer.trace("parent"):
                with self.tracer.trace("child1"):
                    pass
                with self.tracer.trace("child2"):
                    pass

        spans = self.get_spans()
        assert len(spans) == 3

        child1 = spans[0]
        child2 = spans[1]
        parent = spans[2]

        # Verify parent span has process tags
        assert PROCESS_TAGS in parent._meta
        process_tags_value = parent._meta[PROCESS_TAGS]
        assert f"{ENTRYPOINT_BASEDIR_TAG}:to" in process_tags_value
        assert f"{ENTRYPOINT_NAME_TAG}:test_script" in process_tags_value
        assert f"{ENTRYPOINT_TYPE_TAG}:script" in process_tags_value
        assert f"{ENTRYPOINT_WORKDIR_TAG}:workdir" in process_tags_value

        # Verify child1 span has process tags and partial flush marker
        assert PROCESS_TAGS in child1._meta
        assert "_dd.py.partial_flush" in child1._metrics
        assert child1._metrics["_dd.py.partial_flush"] == 2

        assert PROCESS_TAGS not in child2._meta
