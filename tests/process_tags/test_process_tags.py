import pytest

from ddtrace.internal.constants import PROCESS_TAGS
from ddtrace.internal.process_tags import _process_tag_reload
from ddtrace.internal.process_tags import normalize_tag
from ddtrace.settings._config import config
from tests.utils import TracerTestCase


@pytest.mark.parametrize(
    "input,expected",
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
    def test_process_tags_deactivated(self):
        config._process_tags_enabled = False
        _process_tag_reload()

        with self.tracer.trace("test"):
            pass

        span = self.get_spans()[0]
        assert span is not None
        assert PROCESS_TAGS not in span._meta

    @pytest.mark.snapshot
    def test_process_tags_activated(self):
        """Test process tags using override_env instead of run_in_subprocess"""
        config._process_tags_enabled = True
        _process_tag_reload()

        with self.tracer.trace("test"):
            pass

    @pytest.mark.snapshot
    def test_process_tags_only_on_local_root_span(self):
        """Test that only local root spans get process tags, not children"""
        config._process_tags_enabled = True
        _process_tag_reload()
        with self.tracer.trace("parent"):
            with self.tracer.trace("child"):
                pass
