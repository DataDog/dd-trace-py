import sys
from unittest.mock import patch

import pytest

from ddtrace.internal import process_tags
from ddtrace.internal.process_tags import ENTRYPOINT_TYPE_MODULE
from ddtrace.internal.process_tags import ENTRYPOINT_TYPE_SCRIPT
from ddtrace.internal.process_tags import _compute_process_tag
from ddtrace.internal.process_tags import _get_entrypoint_name
from ddtrace.internal.process_tags import _get_entrypoint_type
from ddtrace.internal.process_tags import normalize_tag_value
from tests.utils import override_global_config
from tests.utils import process_tag_reload


TEST_SCRIPT_PATH = "/path/to/test_script.py"
TEST_WORKDIR_PATH = "/path/to/workdir"


@pytest.fixture(autouse=True)
def _restore_process_tags():
    original = process_tags.process_tags
    original_list = process_tags.process_tags_list
    yield
    process_tags.process_tags = original
    process_tags.process_tags_list = original_list


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


def test_module_getattr_raises_attribute_error_for_unknown_process_tags_name():
    with pytest.raises(AttributeError):
        getattr(process_tags, "process_tags_unknown")


@pytest.mark.parametrize(
    "argv0,expected",
    [
        ("python -m unittest", "unittest"),
        ("python3 -m myapp", "myapp"),
        ("python3.11 -W ignore -m mypackage.submodule", "mypackage.submodule"),
        ("python /path/to/my_script.py", "my_script"),
        ("python /srv/app.py --tenant acme", "app"),
    ],
)
def test_get_entrypoint_name_compound_argv0(argv0, expected):
    with patch("sys.argv", [argv0]):
        assert _get_entrypoint_name() == expected


@pytest.mark.parametrize(
    "argv0,expected_type",
    [
        ("python -m unittest", ENTRYPOINT_TYPE_MODULE),
        ("python /path/to/script.py", ENTRYPOINT_TYPE_SCRIPT),
        ("python /srv/app.py -m prod", ENTRYPOINT_TYPE_SCRIPT),
    ],
)
def test_get_entrypoint_type_compound_argv0(argv0, expected_type):
    with patch("sys.argv", [argv0]):
        assert _get_entrypoint_type() == expected_type


def test_svc_auto_compound_argv0_produces_module_name(tracer):
    with patch("sys.argv", ["python -m unittest"]), patch("os.getcwd", return_value=TEST_WORKDIR_PATH):
        process_tag_reload()
        assert "svc.auto:unittest" in process_tags.process_tags


@pytest.mark.skipif(sys.version_info[:2] == (3, 9), reason="sys.orig_argv is not available on Python 3.9")
def test_get_entrypoint_name_module_mode_uses_orig_argv_when_sys_argv_is_incomplete():
    with patch("sys.argv", ["-m"]), patch("sys.orig_argv", ["python", "-m", "myapp"]):
        assert _get_entrypoint_name() == "myapp"
        assert _get_entrypoint_type() == ENTRYPOINT_TYPE_MODULE


@pytest.mark.skipif(sys.version_info[:2] != (3, 9), reason="sys.orig_argv fallback behavior is specific to Python 3.9")
def test_get_entrypoint_name_module_mode_falls_back_to_main_module_on_py39():
    with patch("sys.argv", ["-m"]):
        assert _get_entrypoint_name() == "__main__"
        assert _get_entrypoint_type() == ENTRYPOINT_TYPE_MODULE


@pytest.mark.snapshot
def test_process_tags_activated(tracer):
    with patch("sys.argv", [TEST_SCRIPT_PATH]), patch("os.getcwd", return_value=TEST_WORKDIR_PATH):
        process_tag_reload()

        with tracer.trace("parent"):
            with tracer.trace("child"):
                pass


@pytest.mark.snapshot
@pytest.mark.subprocess(env=dict(DD_SERVICE="foobar"))
def test_process_tags_user_defined_service():
    from unittest.mock import patch

    from tests.utils import process_tag_reload
    from tests.utils import scoped_tracer

    with scoped_tracer() as tracer:
        with patch("sys.argv", ["/path/to/test_script.py"]), patch("os.getcwd", return_value="/path/to/workdir"):
            process_tag_reload()

            with tracer.trace("parent"):
                with tracer.trace("child"):
                    pass


@pytest.mark.snapshot
def test_process_tags_edge_case(tracer):
    with patch("sys.argv", ["/test_script"]), patch("os.getcwd", return_value=TEST_WORKDIR_PATH):
        process_tag_reload()

        with tracer.trace("span"):
            pass


@pytest.mark.snapshot
def test_process_tags_error(tracer):
    with patch("sys.argv", []), patch("os.getcwd", return_value=TEST_WORKDIR_PATH):
        with override_global_config(dict(_telemetry_enabled=False)):
            with patch("ddtrace.internal.process_tags.log") as mock_log:
                process_tag_reload()

                with tracer.trace("span"):
                    pass

                # entrypoint.basedir, entrypoint.name, and svc.auto all fail
                # when sys.argv is empty (IndexError on sys.argv[0])
                assert mock_log.debug.call_count == 3
                failed_tags = {args[0][1] for args in mock_log.debug.call_args_list}
                assert "entrypoint.basedir" in failed_tags
                assert "entrypoint.name" in failed_tags
                assert "svc.auto" in failed_tags


@pytest.mark.snapshot
@pytest.mark.subprocess(env=dict(DD_TRACE_PARTIAL_FLUSH_ENABLED="true", DD_TRACE_PARTIAL_FLUSH_MIN_SPANS="2"))
def test_process_tags_partial_flush():
    from unittest.mock import patch

    from tests.utils import override_global_config
    from tests.utils import process_tag_reload
    from tests.utils import scoped_tracer

    with scoped_tracer() as tracer:
        with patch("sys.argv", ["/path/to/test_script.py"]), patch("os.getcwd", return_value="/path/to/workdir"):
            process_tag_reload()

            with override_global_config(dict(_partial_flush_enabled=True, _partial_flush_min_spans=2)):
                with tracer.trace("parent"):
                    with tracer.trace("child1"):
                        pass
                    with tracer.trace("child2"):
                        pass


@pytest.mark.subprocess()
def test_process_tags_without_reload():
    from ddtrace.internal.constants import PROCESS_TAGS
    from ddtrace.internal.process_tags import ENTRYPOINT_BASEDIR_TAG
    from ddtrace.internal.process_tags import ENTRYPOINT_NAME_TAG
    from ddtrace.internal.process_tags import ENTRYPOINT_TYPE_TAG
    from ddtrace.internal.process_tags import ENTRYPOINT_WORKDIR_TAG
    from tests.utils import scoped_tracer

    with scoped_tracer() as tracer:
        with tracer.trace("test"):
            pass

        span = tracer._span_aggregator.writer.spans[0]

        assert span is not None
        assert span._has_attribute(PROCESS_TAGS)

        process_tags_meta = span._get_str_attribute(PROCESS_TAGS)
        assert ENTRYPOINT_BASEDIR_TAG in process_tags_meta
        assert ENTRYPOINT_NAME_TAG in process_tags_meta
        assert ENTRYPOINT_TYPE_TAG in process_tags_meta
        assert ENTRYPOINT_WORKDIR_TAG in process_tags_meta


@pytest.mark.subprocess(env=dict(DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED="False"))
def test_process_tags_deactivated():
    from ddtrace.internal.constants import PROCESS_TAGS
    from tests.utils import scoped_tracer

    with scoped_tracer() as tracer:
        with tracer.trace("test"):
            pass

        span = tracer._span_aggregator.writer.spans[0]

        assert span is not None
        assert not span._has_attribute(PROCESS_TAGS)
