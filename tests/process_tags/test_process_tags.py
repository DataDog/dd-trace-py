import os
from pathlib import Path
import sys

from ddtrace.internal.constants import PROCESS_TAGS
from ddtrace.internal.process_tags import normalize_tag
from ddtrace.internal.process_tags import process_tags
from ddtrace.internal.process_tags.constants import ENTRYPOINT_BASEDIR_TAG
from ddtrace.internal.process_tags.constants import ENTRYPOINT_NAME_TAG
from ddtrace.internal.process_tags.constants import ENTRYPOINT_TYPE_SCRIPT
from ddtrace.internal.process_tags.constants import ENTRYPOINT_TYPE_TAG
from ddtrace.internal.process_tags.constants import ENTRYPOINT_WORKDIR_TAG
from tests.utils import TracerTestCase
from tests.utils import override_env


def test_normalize_tag():
    assert normalize_tag("HelloWorld") == "helloworld"
    assert normalize_tag("Hello@World!") == "hello_world_"
    assert normalize_tag("HeLLo123") == "hello123"
    assert normalize_tag("hello world") == "hello_world"
    assert normalize_tag("a/b.c_d-e") == "a/b.c_d-e"
    assert normalize_tag("héllø") == "h_ll_"
    assert normalize_tag("") == ""
    assert normalize_tag("💡⚡️") == "___"
    assert normalize_tag("!foo@") == "_foo_"
    assert normalize_tag("123_abc.DEF-ghi/jkl") == "123_abc.def-ghi/jkl"
    assert normalize_tag("Env:Prod-Server#1") == "env_prod-server_1"


class TestProcessTags(TracerTestCase):
    def test_process_tags_deactivated(self):
        with self.tracer.trace("test"):
            pass

        span = self.get_spans()[0]
        assert span is not None
        assert PROCESS_TAGS not in span._meta

    def test_process_tags_activated_with_override_env(self):
        """Test process tags using override_env instead of run_in_subprocess"""
        with override_env(dict(DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED="True")):
            process_tags._enabled = True
            process_tags.reload()

            with self.tracer.trace("test"):
                pass

            process_tags._enabled = False

            span = self.get_spans()[0]
            assert span is not None
            assert PROCESS_TAGS in span._meta

            expected_name = "pytest"
            expected_type = ENTRYPOINT_TYPE_SCRIPT
            expected_basedir = Path(sys.argv[0]).resolve().parent.name
            expected_workdir = os.path.basename(os.getcwd())

            serialized_tags = span._meta[PROCESS_TAGS]
            expected_raw = (
                f"{ENTRYPOINT_WORKDIR_TAG}:{expected_workdir},"
                f"{ENTRYPOINT_BASEDIR_TAG}:{expected_basedir},"
                f"{ENTRYPOINT_NAME_TAG}:{expected_name},"
                f"{ENTRYPOINT_TYPE_TAG}:{expected_type}"
            )
            assert serialized_tags == expected_raw

            tags_dict = dict(tag.split(":", 1) for tag in serialized_tags.split(","))
            assert tags_dict[ENTRYPOINT_NAME_TAG] == expected_name
            assert tags_dict[ENTRYPOINT_TYPE_TAG] == expected_type
            assert tags_dict[ENTRYPOINT_BASEDIR_TAG] == expected_basedir
            assert tags_dict[ENTRYPOINT_WORKDIR_TAG] == expected_workdir

    def test_process_tags_only_on_local_root_span(self):
        """Test that only local root spans get process tags, not children"""
        with override_env(dict(DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED="True")):
            process_tags._enabled = True
            process_tags.reload()

            with self.tracer.trace("parent"):
                with self.tracer.trace("child"):
                    pass

            process_tags._enabled = False

            spans = self.get_spans()
            assert len(spans) == 2

            parent = [s for s in spans if s.name == "parent"][0]
            assert PROCESS_TAGS in parent._meta

            child = [s for s in spans if s.name == "child"][0]
            assert PROCESS_TAGS not in child._meta

    def test_add_process_tag_compute_exception(self):
        """Test error handling when compute raises exception"""
        with override_env(dict(DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED="True")):
            process_tags._enabled = True
            process_tags.reload()

            def failing_compute():
                raise ValueError("Test exception")

            process_tags.add_process_tag("test.tag", compute=failing_compute)

            assert "test.tag" not in process_tags._process_tags

            process_tags.add_process_tag("test.working", value="value")
            assert "test.working" in process_tags._process_tags
            assert process_tags._process_tags["test.working"] == "value"

            process_tags._enabled = False

    def test_serialization_caching(self):
        """Test that serialization is cached and invalidated properly"""
        with override_env(dict(DD_EXPERIMENTAL_PROPAGATE_PROCESS_TAGS_ENABLED="True")):
            process_tags._enabled = True
            process_tags.reload()

            process_tags.get_serialized_process_tags()
            assert process_tags._serialized is not None

            process_tags.add_process_tag("custom.tag", value="test")
            assert process_tags._serialized is None

            result3 = process_tags.get_serialized_process_tags()
            assert "custom.tag:test" in result3

            process_tags._enabled = False
